import atexit
import socket
import typing

import structlog
from confluent_kafka.cimpl import Producer as ConfluentProducer

from kafkian.serde.serialization import Serializer

logger = structlog.get_logger(__name__)


class Producer:
    """
    Kafka producer with configurable key/value serializers.

    Does not subclass directly from Confluent's Producer,
    since it's a cimpl and therefore not mockable.
    """

    DEFAULT_CONFIG = {
        'acks': 'all',
        'api.version.request': True,
        'client.id': socket.gethostname(),
        'log.connection.close': False,
        'max.in.flight': 1,
        'queue.buffering.max.ms': 100,
        'statistics.interval.ms': 15000,
    }

    def __init__(
            self,
            config: typing.Dict,
            value_serializer=Serializer(),
            key_serializer=Serializer(),
            success_callbacks=None,
            error_callbacks=None
    ) -> None:

        self.value_serializer = value_serializer
        self.key_serializer = key_serializer
        self.success_callbacks = success_callbacks
        self.error_callbacks = error_callbacks

        config = {**self.DEFAULT_CONFIG, **config}
        config['on_delivery'] = self._on_delivery
        config['error_cb'] = self._on_error
        config['throttle_cb'] = self._on_throttle
        config['stats_cb'] = self._on_stats

        logger.info("Initializing producer", config=config)
        atexit.register(self._close)
        self._producer_impl = self._init_producer_impl(config)

    @staticmethod
    def _init_producer_impl(config):
        return ConfluentProducer(config)

    def _close(self):
        self.flush()

    def flush(self, timeout=None):
        logger.info("Flushing producer")
        if timeout:
            self._producer_impl.flush(timeout)
        else:
            self._producer_impl.flush()

    def poll(self, timeout=None):
        timeout = timeout or 1
        return self._producer_impl.poll(timeout)

    def produce(self, topic, key, value, sync=False):
        key = self.key_serializer.serialize(key, topic, is_key=True)
        # If value is None, it's a "tombstone" and shall be passed through
        if value is not None:
            value = self.value_serializer.serialize(value, topic)
        self._produce(topic, key, value)
        if sync:
            self.flush()

    def _produce(self, topic, key, value, **kwargs):
        self._producer_impl.produce(topic=topic, value=value, key=key, **kwargs)

    def _on_delivery(self, err, msg):
        if err:
            logger.warning(
                "Producer send failed",
                error_message=str(err),
                topic=msg.topic(),
                key=msg.key(),
                partition=msg.partition()
            )
            if self.error_callbacks:
                for cb in self.error_callbacks:
                    cb(msg, err)
        else:
            logger.debug(
                "Producer send succeeded",
                topic=msg.topic(),
                key=msg.key(),
                partition=msg.partition()
            )
            if self.success_callbacks:
                for cb in self.success_callbacks:
                    cb(err)

    def _on_error(self, error):
        logger.error("Error", error=error)

    def _on_throttle(self, event):
        logger.warning("Throttle", tevent=event)

    def _on_stats(self, stats):
        # logger.debug("Stats", stats=stats)
        import json
        from pprint import pprint
        pprint(json.loads(stats))
