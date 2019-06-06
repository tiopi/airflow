import logging

from flask import request
from flask_appbuilder.forms import DynamicForm

from airflow import settings
from airflow.configuration import conf
from airflow.exceptions import AirflowException
from airflow.hooks.base_hook import BaseHook
from airflow.utils.db import provide_session
from airflow.models import DagBag
from airflow.models import TaskInstance

from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk import configure_scope, add_breadcrumb, init
from sqlalchemy import exc

original_task_init = TaskInstance.__init__
original_clear_xcom = TaskInstance.clear_xcom_data
SCOPE_TAGS = frozenset(("task_id", "dag_id", "execution_date", "ds", "operator"))

@provide_session
def get_task_instance_attr(self, task_id, attr, session=None):
    """
    Retrieve attribute from task.
    """
    TI = TaskInstance
    ti = (
        session.query(TI)
        .filter(
            TI.dag_id == self.dag_id,
            TI.task_id == task_id,
            TI.execution_date == self.execution_date,
        )
        .all()
    )
    if ti:
        attr = getattr(ti[0], attr)
    else:
        attr = None
    return attr


@property
def ds(self):
    """
    Date stamp for task object.
    """
    return self.execution_date.strftime("%Y-%m-%d")


@provide_session
def new_clear_xcom(self, session=None):
    """
    Add breadcrumbs just before task is executed.
    """
    for task in self.task.get_flat_relatives(upstream=True):
        state = get_task_instance_attr(self, task.task_id, "state")
        operation = get_task_instance_attr(self, task.task_id, "operator")
        add_breadcrumb(
            category="data",
            message="Upstream Task: {}, State: {}, Operation: {}".format(
                task.task_id, state, operation
            ),
            level="info",
        )
    original_clear_xcom(self, session)


def add_sentry(self, *args, **kwargs):
    """
    Change the TaskInstance init function to add customized tagging.
    """
    original_task_init(self, *args, **kwargs)
    self.operator = self.task.__class__.__name__
    with configure_scope() as scope:
        for tag_name in SCOPE_TAGS:
            scope.set_tag(tag_name, getattr(self, tag_name))


class SentryHook(BaseHook):
    """
    Wrap around the Sentry SDK.
    """

    def __init__(self, sentry_conn_id=None):
        sentry_celery = CeleryIntegration()
        integrations = [sentry_celery]
        ignore_logger("airflow.task")

        self.dsn = self.get_connection(sentry_conn_id)
        init(dsn=self.dsn, integrations=integrations)

        if not getattr(TaskInstance, "_sentry_integration_", False):
            TaskInstance.__init__ = add_sentry
            TaskInstance.clear_xcom_data = new_clear_xcom
            TaskInstance.ds = ds
            TaskInstance._sentry_integration_ = True