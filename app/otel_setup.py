from __future__ import annotations

import logging
import os

from opentelemetry import _logs, metrics, trace
from opentelemetry.sdk.resources import Resource

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor


OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)


_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

_initialized = False


class _DropOtelInternalLogs(logging.Filter):


    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


def _find_otel_handler(logger: logging.Logger):

    for handler in logger.handlers:
        if type(handler).__module__.startswith("opentelemetry"):
            return handler
    return None


def setup_otel() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    resource = Resource.create()

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
        export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
    )
    _logs.set_logger_provider(logger_provider)


    LoggingInstrumentor().instrument(set_logging_format=True)

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    otel_handler = _find_otel_handler(root)
    if otel_handler is None:
        otel_handler = LoggingHandler(level=LOG_LEVEL, logger_provider=logger_provider)
        root.addHandler(otel_handler)
    otel_handler.addFilter(_DropOtelInternalLogs())

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())

    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).addHandler(otel_handler)

    HTTPXClientInstrumentor().instrument()
    GrpcInstrumentorClient().instrument()
    SystemMetricsInstrumentor().instrument(meter_provider=meter_provider)


def instrument_fastapi(app) -> None:
    FastAPIInstrumentor.instrument_app(app, tracer_provider=trace.get_tracer_provider())