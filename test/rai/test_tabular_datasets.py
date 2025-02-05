# ---------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# ---------------------------------------------------------

import logging
import pathlib
import time

from azure.ai.ml import MLClient, dsl, Input
from azure.ai.ml import load_job
from urllib3 import Timeout

from test.constants_for_test import Timeouts
from test.utilities_for_test import submit_and_wait, process_file

_logger = logging.getLogger(__file__)
logging.basicConfig(level=logging.INFO)


class Testregister_tabular_dataset:
    def test_smoke_registration(self, ml_client: MLClient, component_config):
        version_string = component_config["version"]
        epoch_secs = int(time.time())

        register_tabular_component = ml_client.components.get(
            name="register_tabular_dataset", version=version_string
        )

        @dsl.pipeline(
            compute="cpucluster",
            description="Test of Register Tabular component",
            experiment_name="Smoke_Tabular_Datset_registration",
        )
        def my_pipeline(train_parquet, test_parquet):
            _ = register_tabular_component(
                dataset_input_path=train_parquet,
                dataset_base_name="tabular_train_adult",
                dataset_name_suffix=str(epoch_secs),
            )
            _ = register_tabular_component(
                dataset_input_path=test_parquet,
                dataset_base_name="tabular_test_adult",
                dataset_name_suffix=str(epoch_secs),
            )
            return {}

        pipeline = my_pipeline(
            Input(
                type="uri_file",
                path=f"adult_train_pq:{version_string}",
                mode="download",
            ),
            Input(
                type="uri_file", path=f"adult_test_pq:{version_string}", mode="download"
            ),
        )

        conversion_pipeline_job = submit_and_wait(ml_client, pipeline)
        assert conversion_pipeline_job is not None

    def test_use_tabular_dataset(
        self, ml_client: MLClient, component_config, registered_adult_model_id: str
    ):
        version_string = component_config["version"]
        epoch_secs = int(time.time())
        train_tabular_base = "train_tabular_adult"
        test_tabular_base = "test_tabular_adult"

        register_tabular_component = ml_client.components.get(
            name="register_tabular_dataset", version=version_string
        )

        @dsl.pipeline(
            compute="cpucluster",
            description="Test of Register Tabular component",
            experiment_name="Tabular_Datset_registration",
        )
        def tabular_registration_pipeline(parquet_file, base_name):
            reg_job = register_tabular_component(
                dataset_input_path=parquet_file,
                dataset_base_name=base_name,
                dataset_name_suffix=str(epoch_secs),
            )
            reg_job.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)
            return {}

        adult_train_pq = Input(
            type="uri_file", path=f"adult_train_pq:{version_string}", mode="download"
        )
        pipeline = tabular_registration_pipeline(
            adult_train_pq, base_name=train_tabular_base
        )

        conversion_pipeline_job = submit_and_wait(ml_client, pipeline)
        assert conversion_pipeline_job is not None

        adult_test_pq = Input(
            type="uri_file", path=f"adult_test_pq:{version_string}", mode="download"
        )
        pipeline_2 = tabular_registration_pipeline(
            adult_test_pq, base_name=test_tabular_base
        )
        conversion_pipeline_job_2 = submit_and_wait(ml_client, pipeline_2)
        assert conversion_pipeline_job_2 is not None

        # ----

        # Now we want to consume the dataset in one of our pipelines

        fetch_model_component = ml_client.components.get(
            name="fetch_registered_model", version=version_string
        )

        tabular_to_parquet_component = ml_client.components.get(
            name="convert_tabular_to_parquet", version=version_string
        )

        rai_constructor_component = ml_client.components.get(
            name="rai_insights_constructor", version=version_string
        )

        rai_explanation_component = ml_client.components.get(
            name="rai_insights_explanation", version=version_string
        )

        rai_gather_component = ml_client.components.get(
            name="rai_insights_gather", version=version_string
        )
        _logger.info("Loaded all components: {0}".format(type(rai_gather_component)))

        @dsl.pipeline(
            compute="cpucluster",
            description="Test of Register Tabular component",
            experiment_name=f"Use_Tabular_Dataset_{version_string}",
        )
        def use_tabular_rai(
            target_column_name,
            train_data_name,
            test_data_name,
        ):
            fetch_model_job = fetch_model_component(model_id=registered_adult_model_id)
            fetch_model_job.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            to_parquet_job_train = tabular_to_parquet_component(
                tabular_dataset_name=train_data_name
            )
            to_parquet_job_train.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            to_parquet_job_test = tabular_to_parquet_component(
                tabular_dataset_name=test_data_name
            )
            to_parquet_job_test.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            construct_job = rai_constructor_component(
                title="Run built from DSL",
                task_type="classification",
                model_info_path=fetch_model_job.outputs.model_info_output_path,
                train_dataset=to_parquet_job_train.outputs.dataset_output_path,
                test_dataset=to_parquet_job_test.outputs.dataset_output_path,
                target_column_name=target_column_name,
                categorical_column_names='["Race", "Sex", "Workclass", "Marital Status", "Country", "Occupation"]',
                maximum_rows_for_test_dataset=5000,
                classes="[]",
            )
            construct_job.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            rai_explanation_job = rai_explanation_component(
                rai_insights_dashboard=construct_job.outputs.rai_insights_dashboard,
                comment="Something, something",
            )
            rai_explanation_job.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            rai_gather_job = rai_gather_component(
                constructor=construct_job.outputs.rai_insights_dashboard,
                insight_1=rai_explanation_job.outputs.explanation,
            )
            rai_gather_job.set_limits(timeout=Timeouts.DEFAULT_TIMEOUT)

            rai_gather_job.outputs.dashboard.mode = "upload"
            rai_gather_job.outputs.ux_json.mode = "upload"

            return {
                "dashboard": rai_gather_job.outputs.dashboard,
                "ux_json": rai_gather_job.outputs.ux_json,
            }

        train_data_name = f"{train_tabular_base}_{epoch_secs}"
        test_data_name = f"{test_tabular_base}_{epoch_secs}"

        rai_pipeline = use_tabular_rai(
            target_column_name="income",
            train_data_name=train_data_name,
            test_data_name=test_data_name,
        )

        rai_pipeline_job = submit_and_wait(ml_client, rai_pipeline)
        assert rai_pipeline_job is not None

        # ----

        # Now do the same thing from a YAML file
        current_dir = pathlib.Path(__file__).parent.absolute()
        pipeline_file = current_dir / "pipeline_fetch_tabular.yaml"
        pipeline_processed_file = "pipeline_fetch_tabular.processed.yaml"

        replacements = {
            "VERSION_REPLACEMENT_STRING": str(component_config["version"]),
            "MODEL_ID_REPLACEMENT_STRING": registered_adult_model_id,
            "TRAIN_TABULAR_REPLACEMENT_STRING": train_data_name,
            "TEST_TABULAR_REPLACEMENT_STRING": test_data_name,
        }
        process_file(pipeline_file, pipeline_processed_file, replacements)

        pipeline_job = load_job(path=pipeline_processed_file)

        submit_and_wait(ml_client, pipeline_job)
