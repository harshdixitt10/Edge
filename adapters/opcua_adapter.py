"""
OPC-UA Adapter — connects to OPC-UA servers and subscribes to node data changes.

Uses the asyncua library (pure-async OPC-UA client).
Emits DataEvent objects onto the EventBus for each data change.
Supports derived tags via ExpressionEngine.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from adapters.base_adapter import BaseAdapter
from core.event_bus import EventBus
from core.expression_engine import ExpressionEngine
from core.models import DataEvent, DerivedTag, OpcuaAdapterConfig

logger = logging.getLogger(__name__)


class DerivedTagEvaluator:
    """Evaluates derived tags from current source tag values.

    Maintains a cache of latest values for each source tag.
    After each scan, evaluates all derived tag expressions and
    emits them as additional DataEvents.
    """

    def __init__(self, derived_tags: list[DerivedTag], engine: ExpressionEngine):
        self.derived_tags = derived_tags
        self.engine = engine
        # tag_id -> latest value
        self._tag_values: dict[str, Any] = {}

    def update_tag(self, tag_id: str, value: Any) -> None:
        """Update the cached value for a tag."""
        self._tag_values[tag_id] = value

    def evaluate_all(self) -> list[tuple[str, Any]]:
        """Evaluate all derived tags and return (tag_id, computed_value) pairs."""
        results: list[tuple[str, Any]] = []
        for dt in self.derived_tags:
            try:
                # Build variable map from source tag values
                variables = {}
                all_available = True
                for src_tag_id in dt.source_tag_ids:
                    if src_tag_id in self._tag_values:
                        variables[src_tag_id] = self._tag_values[src_tag_id]
                    else:
                        all_available = False

                if not all_available:
                    continue  # Skip if not all source values are available yet

                value = self.engine.evaluate(dt.expression_js, variables)
                results.append((dt.tag_id, value))
            except Exception as e:
                logger.warning(f"Derived tag '{dt.tag_id}' evaluation failed: {e}")
        return results


class SubscriptionHandler:
    """Handles OPC-UA subscription data change notifications."""

    def __init__(
        self,
        bus: EventBus,
        adapter_name: str,
        thing_key: str,
        tag_map: dict,
        derived_evaluator: Optional[DerivedTagEvaluator] = None,
        metric_map: Optional[dict] = None,
    ):
        self.bus = bus
        self.adapter_name = adapter_name
        self.thing_key = thing_key
        self.tag_map = tag_map  # node_id -> {tag_id, metric_id, namespace}
        self.derived_evaluator = derived_evaluator
        self.metric_map = metric_map or {}  # tag_id -> metric_id

    def datachange_notification(self, node, val, data):
        """Called by asyncua on subscription data change.

        tag_map maps node_id → list[tag_info] so that multiple tags
        configured on the same OPC-UA node all get their events emitted.
        """
        try:
            node_id_str = str(node.nodeid.Identifier)
            # tag_map values are lists — one entry per tag mapped to this node
            tag_infos = self.tag_map.get(node_id_str)
            if not tag_infos:
                logger.debug(f"Received notification for unmapped node '{node_id_str}' — skipping")
                return

            loop = asyncio.get_event_loop()
            now = datetime.now(timezone.utc)
            safe_val = val if val is not None else 0

            for tag_info in tag_infos:
                tag_id = tag_info.get("tag_id", "")
                metric_id = tag_info.get("metric_id", "")

                event = DataEvent(
                    id=str(uuid.uuid4()),
                    adapter_name=self.adapter_name,
                    thing_key=self.thing_key,
                    node_id=node_id_str,
                    namespace=node.nodeid.NamespaceIndex,
                    tag_id=tag_id,
                    metric_id=metric_id,
                    value=safe_val,
                    quality="Good",
                    timestamp=now,
                )
                loop.create_task(self.bus.publish(event))

                # Update derived tag evaluator with new value
                if self.derived_evaluator and tag_id:
                    self.derived_evaluator.update_tag(tag_id, safe_val)

            # Evaluate derived tags once after all tag values are updated
            if self.derived_evaluator:
                derived_results = self.derived_evaluator.evaluate_all()
                for d_tag_id, d_value in derived_results:
                    d_event = DataEvent(
                        id=str(uuid.uuid4()),
                        adapter_name=self.adapter_name,
                        thing_key=self.thing_key,
                        node_id=f"derived:{d_tag_id}",
                        tag_id=d_tag_id,
                        metric_id=self.metric_map.get(d_tag_id, ""),
                        value=d_value,
                        quality="Good",
                        timestamp=now,
                    )
                    loop.create_task(self.bus.publish(d_event))

        except Exception as e:
            logger.error(f"Error in OPC-UA data change handler: {e}")


class OPCUAAdapter(BaseAdapter):
    """OPC-UA protocol adapter using asyncua."""

    def __init__(self, adapter_id: str, name: str, config: dict, bus: EventBus):
        super().__init__(adapter_id, name, config, bus)
        self._client = None
        self._subscriptions = []
        self._tasks = []
        self._adapter_config: Optional[OpcuaAdapterConfig] = None
        self._expression_engine = ExpressionEngine()

    async def connect(self) -> None:
        """Connect to the OPC-UA server."""
        try:
            from asyncua import Client

            self._adapter_config = OpcuaAdapterConfig(**self.config)

            if not self._adapter_config.thing_configs:
                raise ValueError("No thing_configs defined in adapter configuration")

            # Use the first thing's connection for now
            thing = self._adapter_config.thing_configs[0]
            if not thing.source_tags:
                raise ValueError("No source_tags defined in thing configuration")

            conn = thing.source_tags[0].protocol_connection
            self._client = Client(url=conn.server_url, timeout=conn.timeout_millis / 1000)

            # Set security if not anonymous
            if conn.security_policy_uri and "None" not in conn.security_policy_uri:
                logger.info(f"Setting OPC-UA security policy: {conn.security_policy_uri}")

            if conn.auth_mechanism == "username" and conn.auth_username:
                self._client.set_user(conn.auth_username)
                self._client.set_password(conn.auth_password)

            await self._client.connect()
            logger.info(f"OPC-UA connected to {conn.server_url}")

        except ImportError:
            logger.warning("asyncua not installed — OPC-UA adapter running in simulation mode")
            self._client = None
        except Exception as e:
            logger.error(f"OPC-UA connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from the OPC-UA server."""
        for task in self._tasks:
            task.cancel()
        self._tasks = []

        if self._client:
            try:
                for sub in self._subscriptions:
                    try:
                        await sub.delete()
                    except Exception as e:
                        logger.warning(f"Error deleting subscription: {e}")
                await self._client.disconnect()
            except Exception as e:
                logger.warning(f"Error during OPC-UA disconnect: {e}")
            finally:
                self._client = None
                self._subscriptions = []

    async def run(self) -> None:
        """Subscribe to configured nodes and process data changes."""
        if not self._adapter_config:
            self._adapter_config = OpcuaAdapterConfig(**self.config)

        for thing in self._adapter_config.thing_configs:
            if thing.disabled:
                continue

            if self._client:
                await self._run_subscribed(thing)
            else:
                task = asyncio.create_task(self._run_simulated(thing))
                self._tasks.append(task)

        # Keep alive while running and actively monitor connection state
        while self.running:
            if self._client:
                try:
                    # Ping the server to ensure connection is still alive
                    await self._client.nodes.server_state.read_value()
                except Exception as e:
                    logger.error(f"OPC-UA watchdog detected connection loss: {e}")
                    raise ConnectionError("OPC-UA client disconnected unexpectedly")
                    
            await asyncio.sleep(5)

    async def _run_subscribed(self, thing) -> None:
        """Run with real OPC-UA subscription."""
        if not thing.source_tags:
            return

        source = thing.source_tags[0]

        # Build metric_map: tag_id → metric_id
        metric_map = {m.tag_id: m.metric_id for m in thing.metric_mappings}

        # Build tag_map: node_id → list[tag_info]
        # Using a list per node_id so that multiple tags mapped to the same
        # OPC-UA node all receive their data change events (no silent overwrites).
        tag_map: dict[str, list[dict]] = {}
        for tag in source.read_tags:
            entry = {
                "tag_id": tag.tag_id,
                "metric_id": metric_map.get(tag.tag_id, ""),
                "namespace": tag.namespace,
            }
            if tag.node_id not in tag_map:
                tag_map[tag.node_id] = [entry]
            else:
                # Same node_id used by multiple tags — keep all of them
                logger.warning(
                    f"Node '{tag.node_id}' mapped to multiple tags: "
                    f"{[t['tag_id'] for t in tag_map[tag.node_id]]} + '{tag.tag_id}' "
                    f"— all will receive data change events"
                )
                tag_map[tag.node_id].append(entry)

        # Create derived tag evaluator if derived tags are configured
        derived_evaluator = None
        if thing.derived_tags:
            derived_evaluator = DerivedTagEvaluator(thing.derived_tags, self._expression_engine)
            logger.info(
                f"Derived tags configured for '{thing.name}': "
                f"{[dt.tag_id for dt in thing.derived_tags]}"
            )

        handler = SubscriptionHandler(
            self.bus, thing.name, thing.thing_key, tag_map,
            derived_evaluator=derived_evaluator,
            metric_map=metric_map,
        )

        sub = await self._client.create_subscription(
            period=thing.scan_interval_ms,
            handler=handler,
        )
        self._subscriptions.append(sub)

        # Subscribe to unique OPC-UA nodes only (tag_map keys are already deduplicated).
        # Each node_id is subscribed once; tag_map holds ALL tags for that node.
        nodes = []
        for node_id_str, tag_infos in tag_map.items():
            namespace = tag_infos[0]["namespace"]  # all infos for a node share the same ns
            try:
                node = self._client.get_node(f"ns={namespace};i={node_id_str}")
                nodes.append(node)
            except Exception as e:
                logger.warning(f"Cannot get node {node_id_str}: {e}")

        if nodes:
            await sub.subscribe_data_change(nodes)
            logger.info(
                f"Subscribed to {len(nodes)} OPC-UA node(s) for '{thing.name}' "
                f"({len(source.read_tags)} tag(s) mapped)"
            )

    async def _run_simulated(self, thing) -> None:
        """Simulated mode when asyncua is not available — generates test data."""
        import random

        logger.info(f"Running adapter '{thing.name}' in SIMULATION mode")
        metric_map = {m.tag_id: m.metric_id for m in thing.metric_mappings}

        # Create derived tag evaluator
        derived_evaluator = None
        if thing.derived_tags:
            derived_evaluator = DerivedTagEvaluator(thing.derived_tags, self._expression_engine)
            logger.info(
                f"Derived tags configured for '{thing.name}' (sim): "
                f"{[dt.tag_id for dt in thing.derived_tags]}"
            )

        while self.running:
            if thing.source_tags:
                for source in thing.source_tags:
                    for tag in source.read_tags:
                        value = round(random.uniform(0, 100), 2)
                        event = DataEvent(
                            adapter_name=thing.name,
                            thing_key=thing.thing_key,
                            node_id=tag.node_id,
                            namespace=tag.namespace,
                            tag_id=tag.tag_id,
                            metric_id=metric_map.get(tag.tag_id, ""),
                            value=value,
                            quality="Good",
                        )
                        await self.bus.publish(event)

                        # Update derived evaluator
                        if derived_evaluator:
                            derived_evaluator.update_tag(tag.tag_id, value)

                # Evaluate derived tags after all source tags are scanned
                if derived_evaluator:
                    derived_results = derived_evaluator.evaluate_all()
                    for d_tag_id, d_value in derived_results:
                        d_event = DataEvent(
                            adapter_name=thing.name,
                            thing_key=thing.thing_key,
                            node_id=f"derived:{d_tag_id}",
                            tag_id=d_tag_id,
                            metric_id=metric_map.get(d_tag_id, ""),
                            value=d_value,
                            quality="Good",
                        )
                        await self.bus.publish(d_event)
            else:
                # Default simulation with dummy data
                event = DataEvent(
                    adapter_name=thing.name,
                    thing_key=thing.thing_key,
                    node_id="sim_001",
                    value=round(random.uniform(0, 100), 2),
                    quality="Good",
                )
                await self.bus.publish(event)

            await asyncio.sleep(thing.scan_interval_ms / 1000)


async def test_opcua_connection(connection_config: dict) -> dict:
    """Test an OPC-UA connection without starting the full adapter.
    
    Returns dict with: success, message, server_info (if connected).
    """
    try:
        from asyncua import Client

        client = Client(
            url=connection_config.get("server_url", ""),
            timeout=connection_config.get("timeout_millis", 10000) / 1000,
        )

        if connection_config.get("auth_mechanism") == "username":
            client.set_user(connection_config.get("auth_username", ""))
            client.set_password(connection_config.get("auth_password", ""))

        await asyncio.wait_for(client.connect(), timeout=connection_config.get("timeout_millis", 10000) / 1000)

        # Get server info
        server_node = client.get_node("i=2261")  # ServerStatus node
        try:
            server_status = await server_node.read_value()
            info = str(server_status)
        except Exception:
            info = "Connected (server info unavailable)"

        await client.disconnect()

        return {
            "success": True,
            "message": "Successfully connected to OPC-UA server",
            "server_info": info,
        }
    except ImportError:
        return {
            "success": False,
            "message": "asyncua library not installed. Install with: pip install asyncua",
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": f"Connection timed out after {connection_config.get('timeout_millis', 10000)}ms",
        }
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}

