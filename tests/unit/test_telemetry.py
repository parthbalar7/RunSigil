from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from runsigil_telemetry import Operation


def test_genai_operation_emits_privacy_safe_span() -> None:
    exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[metric_reader]))

    with Operation(
        "execute_tool demo.invoice.send",
        metric_name="gen_ai.execute_tool.duration",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "demo.invoice.send",
            "runsigil.content_captured": False,
        },
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "execute_tool demo.invoice.send"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    serialized = repr(dict(span.attributes))
    assert "recipient" not in serialized
    assert "prompt" not in serialized
    assert "output" not in serialized
    metric_data = metric_reader.get_metrics_data()
    names = {
        metric.name
        for resource_metric in metric_data.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert "gen_ai.execute_tool.duration" in names
