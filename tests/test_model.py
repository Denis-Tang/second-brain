import json

import httpx
import pytest

from shared_brain.model import ModelClient, ModelError


def test_local_service_without_key_and_fenced_result(monkeypatch):
    requests = []
    source = {"id": "source-1", "title": "Reading note", "content": "Reported result: smaller batches fit."}
    note = {"title": "Batch size", "body": "The source reports that smaller batches fit.", "source_ids": ["source-1"]}
    responses = iter(["OK", "```json\n" + json.dumps([note]) + "\n```", "[]"])

    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": next(responses)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    client = ModelClient("http://localhost:1234/v1/", "local-model")
    assert requests == []
    assert client.test_connection() == {"message": "模型连接成功。"}
    assert client.extract([source], [{"title": "Old", "body": "Other knowledge", "project": "demo"}]) == [{**note, "kind": "memory", "conflict": False, "conditions": {}, "merge_ids": []}]
    assert client.extract([source]) == []
    assert client.extract([]) == []
    assert len(requests) == 3
    for url, kwargs in requests:
        assert url == "http://localhost:1234/v1/chat/completions"
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["json"]["model"] == "local-model"
        assert "response_format" not in kwargs["json"]
    material = json.loads(requests[1][1]["json"]["messages"][1]["content"])
    assert material["sources"] == [source]
    assert material["existing"][0]["project"] == "demo"


def test_rejects_unknown_source_id(monkeypatch):
    def post(url, **kwargs):
        assert kwargs["headers"] == {"Authorization": "Bearer test-placeholder"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps([
                {"title": "Unknown source", "body": "A result", "source_ids": ["invented-id"]}
            ])}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    client = ModelClient("http://localhost:1234/v1", "local-model", "test-placeholder")
    with pytest.raises(ModelError, match="无效来源"):
        client.extract([{"id": "source-1", "title": "Input", "content": "An observation."}])
