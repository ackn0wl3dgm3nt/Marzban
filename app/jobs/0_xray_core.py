import time
import traceback

from app import app, logger, scheduler, xray
from app.db import GetDB, crud
from app.models.node import NodeStatus
from config import JOB_CORE_HEALTH_CHECK_INTERVAL
from xray_api import exc as xray_exc


def core_health_check():
    config = None

    # main core
    if not xray.core.started:
        if not config:
            config = xray.config.include_db_users()
        xray.core.restart(config)

    # nodes' core
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            try:
                assert node.started
                node.api.get_sys_stats(timeout=2)
            except (ConnectionError, xray_exc.XrayError, AssertionError):
                if not config:
                    config = xray.config.include_db_users()
                xray.operations.restart_node(node_id, config)

        if not node.connected:
            if not config:
                config = xray.config.include_db_users()
            xray.operations.connect_node(node_id, config)


@app.on_event("startup")
async def start_core():
    logger.info("Generating Xray core config")

    start_time = time.time()
    config = xray.config.include_db_users()
    logger.info(f"Xray core config generated in {(time.time() - start_time):.2f} seconds")

    # main core
    logger.info("Starting main Xray core")
    try:
        xray.core.start(config)
    except Exception:
        traceback.print_exc()

    # Start XrayManager (async)
    logger.info("Starting XrayManager")
    try:
        await xray.xray_manager.start(xray.config)
    except Exception:
        traceback.print_exc()
        logger.error("Failed to start XrayManager, falling back to legacy operations")

    # nodes' core
    logger.info("Starting nodes Xray core")
    with GetDB() as db:
        dbnodes = crud.get_nodes(db=db, enabled=True)
        for dbnode in dbnodes:
            crud.update_node_status(db, dbnode, NodeStatus.connecting)

    # Connect nodes: start xray process, then XrayManager for gRPC
    with GetDB() as db:
        for dbnode in crud.get_nodes(db=db, enabled=True):
            xray.operations.connect_node(dbnode.id, config)

            if xray.xray_manager.is_started:
                try:
                    await xray.xray_manager.connect_node(dbnode)
                except Exception as e:
                    logger.warning(f"XrayManager failed to connect to node {dbnode.id}: {e}")

    scheduler.add_job(core_health_check, 'interval',
                      seconds=JOB_CORE_HEALTH_CHECK_INTERVAL,
                      coalesce=True, max_instances=1)


@app.on_event("shutdown")
async def app_shutdown():
    # Stop XrayManager first (flushes pending operations)
    if xray.xray_manager.is_started:
        logger.info("Stopping XrayManager")
        await xray.xray_manager.stop()

    logger.info("Stopping main Xray core")
    xray.core.stop()

    logger.info("Stopping nodes Xray core")
    for node in list(xray.nodes.values()):
        try:
            node.disconnect()
        except Exception:
            pass
