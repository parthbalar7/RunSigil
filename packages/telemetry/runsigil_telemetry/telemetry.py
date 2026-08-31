from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from time import monotonic
from types import TracebackType
from typing import Any, cast

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

_configure_lock = threading.Lock()
_configured = False
_histograms: dict[str, Histogram] = {}


@dataclass(frozen=True)
class TelemetryConfig:
    service_name: str
    enabled: bool = False
    otlp_http_endpoint: str = "http://localhost:4318"
    service_version: str = "0.2.0"


def configure_telemetry(config: TelemetryConfig) -> bool:
    global _configured
    if not config.enabled:
        return False
    with _configure_lock:
        if _configured:
            return True
        endpoint = config.otlp_http_endpoint.rstrip("/")
        resource = Resource.create(
            {
                "service.name": config.service_name,
                "service.namespace": "runsigil",
                "service.version": config.service_version,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(tracer_provider)
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
        _configured = True
        return True


def _histogram(name: str, unit: str, description: str) -> Histogram:
    histogram = _histograms.get(name)
    if histogram is None:
        histogram = metrics.get_meter("io.runsigil.governance", "0.2.0").create_histogram(
            name=name,
            unit=unit,
            description=description,
        )
        _histograms[name] = histogram
    return histogram


class Operation(AbstractContextManager["Operation"]):
    """A privacy-safe span and duration metric for one governance operation."""

    def __init__(
        self,
        span_name: str,
        *,
        metric_name: str,
        attributes: Mapping[str, str | int | float | bool],
        span_kind: SpanKind = SpanKind.INTERNAL,
    ) -> None:
        self._span_name = span_name
        self._metric_name = metric_name
        self._attributes = dict(attributes)
        self._span_kind = span_kind
        self._manager: Any = None
        self._span: Any = None
        self._started = 0.0

    def __enter__(self) -> Operation:
        self._started = monotonic()
        self._manager = trace.get_tracer("io.runsigil.governance", "0.2.0").start_as_current_span(
            self._span_name,
            kind=self._span_kind,
            attributes=self._attributes,
        )
        self._span = self._manager.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        elapsed = monotonic() - self._started
        metric_attributes = dict(self._attributes)
        if exc_value is not None:
            metric_attributes["error.type"] = type(exc_value).__name__
            self._span.set_status(Status(StatusCode.ERROR))
            self._span.record_exception(exc_value)
        _histogram(
            self._metric_name,
            "s",
            f"Duration of {self._span_name} operations.",
        ).record(elapsed, attributes=metric_attributes)
        return cast(bool | None, self._manager.__exit__(exc_type, exc_value, traceback))


def current_trace_identifiers(run_id: Any) -> tuple[str, str, str | None]:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        return f"{context.trace_id:032x}", f"{context.span_id:016x}", None
    stable_trace = hashlib.sha256(f"runsigil-run:{run_id}".encode()).hexdigest()[:32]
    stable_span = hashlib.sha256(f"runsigil-event:{run_id}:{monotonic()}".encode()).hexdigest()[:16]
    return stable_trace, stable_span, None
