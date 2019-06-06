# -*- coding: utf-8 -*-
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import logging
from typing import Any

from airflow import configuration
from airflow.utils.db import provide_session


log = logging.getLogger(__name__)


class DummySentry:
    """
    Blank class for Sentry logging.
    """

    @classmethod
    def add_tagging(cls, task_instance):
        pass

    @classmethod
    def add_breadcrumbs(cls, session=None):
        pass


@provide_session
def get_task_instance(task, execution_date, session=None):
    """
    Retrieve attribute from task.
    """
    from airflow.models.taskinstance import TaskInstance  # Avoid circular import

    if session is None:
        return None
    TI = TaskInstance
    ti = (
        session.query(TI)
        .filter(
            TI.dag_id == task.dag_id,
            TI.task_id == task.task_id,
            TI.execution_date == execution_date,
        )
        .first()
    )
    return ti


class ConfiguredSentry:
    """
    Configure Sentry SDK.
    """

    SCOPE_TAGS = frozenset(("task_id", "dag_id", "execution_date", "operator"))

    def __init__(self):
        """
        Initialize the Sentry SDK.
        """

        integrations = []
        ignore_logger("airflow.task")
        executor_name = configuration.conf.get("core", "EXECUTOR")

        if executor_name == "CeleryExecutor":
            from sentry_sdk.integrations.celery import CeleryIntegration

            sentry_celery = CeleryIntegration()
            integrations = [sentry_celery]
        else:
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_logging = LoggingIntegration(
                level=logging.INFO, event_level=logging.ERROR
            )
            integrations = [sentry_logging]

        self.task = None
        self.execution_date = None

        # Setting up Sentry using environment variables.
        init(integrations=integrations)

    def add_tagging(self, task_instance):
        """
        Function to add tagging for a task_instance.
        """
        self.task = task_instance.task
        self.execution_date = task_instance.execution_date

        with configure_scope() as scope:
            for tag_name in self.SCOPE_TAGS:
                attribute = getattr(task_instance, tag_name)
                if tag_name == "operator":
                    attribute = self.task.__class__.__name__
                scope.set_tag(tag_name, attribute)

    @provide_session
    def add_breadcrumbs(self, session=None):
        """
        Function to add breadcrumbs inside of a task_instance.
        """

        if self.task is None or session is None:
            return

        for task in self.task.get_flat_relatives(upstream=True):
            task_instance = get_task_instance(
                task, self.execution_date, session=session
            )
            if task_instance is not None:
                operator = self.task.__class__.__name__
                add_breadcrumb(
                    category="upstream_tasks",
                    message="Upstream Task: {ti.dag_id}.{ti.task_id}, "
                    "Execution: {ti.execution_date}, State:[{ti.state}], Operation: {operator}".format(
                        ti=task_instance, operator=operator
                    ),
                    level="info",
                )


Sentry = DummySentry  # type: Any

try:
    from sentry_sdk.integrations.logging import ignore_logger
    from sentry_sdk import configure_scope, add_breadcrumb, init

    Sentry = ConfiguredSentry()

except ImportError as e:
    log.info("Could not configure Sentry: %s, using DummySentry instead.", e)
