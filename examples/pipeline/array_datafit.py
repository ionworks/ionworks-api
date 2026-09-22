"""Submit an ArrayDataFit pipeline that fits the same model separately at
each value of an independent variable.

The keys of ``objectives`` are the independent-variable values (here:
ambient temperature in K). For each key, a separate fit is run with the
corresponding objective, and the resulting fitted parameter is returned
as a 2xN array: the independent-variable values on one row and the
fitted values on the other.
"""

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

# Define entry configuration to provide known parameter values
entry_config = {"values": parameter_values}

# Define array-datafit configuration. The objectives dict is keyed by the
# independent-variable value (here: ambient temperature in K). Each entry
# is fit independently.
array_datafit_config = {
    "objectives": {
        298.15: {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_config_1C},
            "parameters": {"Ambient temperature [K]": 298.15},
        },
        308.15: {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_config_05C},
            "parameters": {"Ambient temperature [K]": 308.15},
        },
    },
    "parameters": {
        "Positive particle diffusivity [m2.s-1]": {
            "bounds": [1e-15, 1e-13],
            "initial_value": 1e-14,
        },
    },
    "cost": {"type": "RMSE"},
    "optimizer": {"type": "ScipyDifferentialEvolution"},
}

# Create pipeline config
pipeline_config = {
    "elements": {
        "entry": {**entry_config, "element_type": "entry"},
        "fit per temperature": {
            **array_datafit_config,
            "element_type": "array_data_fit",
        },
    },
}

# Submit pipeline
pipeline = client.pipeline.create(pipeline_config)
print("Pipeline", pipeline)

# Poll for results
while True:
    pipeline = client.pipeline.get(pipeline.id)
    print(f"Pipeline {pipeline.id} status: {pipeline.status}")
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print("Pipeline completed")
        # The fitted parameter is a 2xN array: row 0 = independent-variable
        # values, row 1 = fitted parameter values.
        print("Result:", result.element_results["fit per temperature"])
        break
    elif pipeline.status == "failed":
        print("Pipeline failed")
        print("Error:", pipeline.error)
        break
    time.sleep(1)
