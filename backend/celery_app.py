from celery import Celery
from backend.config import settings

celery_app = Celery(
    "rfq_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Worker tu dong discover tasks trong module nay
    imports=["backend.tasks.email_tasks"],

    # Gioi han thoi gian cho moi task (tranh hang vinh vien)
    task_soft_time_limit=300,   # soft limit: 5 phut (raise SoftTimeLimitExceeded)
    task_time_limit=360,        # hard kill: 6 phut

    # Celery Beat schedule: poll email dinh ky
    beat_schedule={
        "poll-active-rfqs-every-60s": {
            "task": "tasks.poll_all_active_rfqs",
            "schedule": settings.imap_poll_interval_seconds,  # default 60s
        },
    },
)
