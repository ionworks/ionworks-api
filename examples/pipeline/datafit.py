import json
import os
import sys
import time

from ionworks import Ionworks

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# Setup Ionworks client and pipeline
client = Ionworks()

with open("examples/data/Chen2020.json") as f:
    parameter_values = json.load(f)

data_config_05C = "file:examples/data/chen_synthetic_0.5C/time_series.csv"
data_config_1C = "file:examples/data/chen_synthetic_1C/time_series.csv"

# Define entry configuration to provide parameters
entry_config = {"values": parameter_values}

# Define datafit configuration
datafit_config = {
    "objectives": {
        "test_1C": {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_config_1C},
            "parameters": {"Ambient temperature [K]": 298.15},
        },
        "test_0.5C": {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_config_05C},
            "parameters": {"Ambient temperature [K]": 298.15},
        },
    },
    "parameters": {
        "Negative particle diffusivity [m2.s-1]": {
            "bounds": [1e-14, 1e-13],
            "initial_value": 2e-14,
        },
        "Positive particle diffusivity [m2.s-1]": {
            "bounds": [1e-15, 1e-14],
            "initial_value": 2e-15,
        },
    },
    "cost": {"type": "RMSE"},
    "optimizer": {"type": "ScipyDifferentialEvolution"},
}

# Create pipeline config
pipeline_config = {
    "elements": {
        "entry": {**entry_config, "element_type": "entry"},
        "fit data": {**datafit_config, "element_type": "data_fit"},
    },
}

# Submit pipeline
pipeline = client.pipeline.create(pipeline_config)
print("Pipeline", pipeline)

# Get job results
while True:
    pipeline = client.pipeline.get(pipeline.id)
    print(f"Pipeline {pipeline.id} status: {pipeline.status}")
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print("Pipeline completed")
        print("Result:", result.element_results["fit data"])
        break
    elif pipeline.status == "failed":
        print("Pipeline failed")
        print("Error:", pipeline.error)
        break
    time.sleep(1)
