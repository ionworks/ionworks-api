import json
import time

from ionworks import Ionworks

# Setup Ionworks client and pipeline
client = Ionworks()

# Define configurations
with open("examples/data/Chen2020.json") as f:
    parameter_values = json.load(f)
    parameter_values["Negative electrode loading [A.h.cm-2]"] = 0.00568

# Option 1: direct access to data
data_1C = "file:examples/data/chen_synthetic_1C/time_series.csv"
data_05C = "file:examples/data/chen_synthetic_0.5C/time_series.csv"

# Comment out the following to use database access to data
# To use this you must have run 0_upload.py to upload the data to the database, which
# will have saved the measurement ids to the file
# examples/data/.local/measurement_ids.json
# with open("examples/data/.local/measurement_ids.json") as f:
#     measurement_ids = json.load(f)
#     data_1C = f"db:{measurement_ids['1C discharge']}"
#     data_05C = f"db:{measurement_ids['0.5C discharge']}"

entry_config = {"values": parameter_values}

datafit_config = {
    "objectives": {
        "test": {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_1C},
            "parameters": {"Ambient temperature [K]": 298.15},
        }
    },
    "parameters": {
        "Negative particle diffusivity [m2.s-1]": {
            "bounds": (1e-14, 1e-13),
            "initial_value": 2e-14,
        },
        "Positive particle diffusivity [m2.s-1]": {
            "bounds": (1e-15, 1e-14),
            "initial_value": 2e-15,
        },
    },
    "cost": {"type": "RMSE"},
    "optimizer": {"type": "ScipyDifferentialEvolution"},
}
validation_config = {
    "objectives": {
        "test": {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_05C},
        }
    },
    "summary_stats": [{"type": "RMSE"}, {"type": "MAE"}, {"type": "Max"}],
}

calculation_config = {
    "calculation": "ElectrodeVolumeFractionFromLoading",
    "electrode": "negative",
    "method": "loading",
}

# Create pipeline config with all element types
pipeline_config = {
    "name": "API Demo Pipeline",
    "elements": {
        "load file": {**entry_config, "element_type": "entry"},
        "calculate diffusivity": {**calculation_config, "element_type": "calculation"},
        "fit data": {**datafit_config, "element_type": "data_fit"},
        "validate": {**validation_config, "element_type": "validation"},
    },
}

# Submit pipeline
pipeline = client.pipeline.create(pipeline_config)


# Get job results
while True:
    pipeline = client.pipeline.get(pipeline.id)
    # print("Pipeline", pipeline)
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print("Pipeline completed")
        print("Result:", result.result)
        # print("Element results:", result.element_results)
        break
    elif pipeline.status == "failed":
        print("Pipeline failed")
        print("Error:", pipeline.error)
        break
    time.sleep(1)
