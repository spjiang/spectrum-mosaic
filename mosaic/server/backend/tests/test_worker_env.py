from app.config import Settings
from app.services.worker_env import apply_worker_env, worker_env_view


def test_apply_worker_env_overrides_data_roots():
    settings = Settings(data_roots="/old", default_input_dir="", default_output_dir="")

    class _Row:
        value = {"data_roots": "/new", "default_input_dir": "/in", "default_output_dir": "/out"}

    class _Db:
        def get(self, _cls, _key):
            return _Row()

    out = apply_worker_env(_Db(), settings)  # type: ignore[arg-type]
    assert out.data_roots == "/new"
    assert out.default_input_dir == "/in"
    assert out.default_output_dir == "/out"


def test_worker_env_view_readonly_mq():
    settings = Settings(rabbitmq_url="amqp://mosaic:mosaic_secret@rabbitmq:5672/")
    view = worker_env_view(None, settings)
    assert view["rabbitmq_url"].startswith("amqp://")
    assert view["workdir"] == "/app"
    assert view["source"] == "env"
