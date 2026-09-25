import json
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ionworks import Ionworks

# Setup Ionworks client and pipeline
client = Ionworks()

# Choose how to access the data
# DATA_ACCESS_METHOD = "database"
DATA_ACCESS_METHOD = "direct"

data_config = "file:examples/data/chen_synthetic_0.5C/time_series.csv"

with open("examples/data/Chen2020.json") as f:
    parameter_values = json.load(f)

# Define entry configuration to provide parameters
entry_config = {"values": parameter_values}

# Define validation configuration
validation_config = {
    "objectives": {
        "test": {
            "objective": "CurrentDriven",
            "model": {"type": "SPMe"},
            "data": {"data": data_config},
        }
    },
    "summary_stats": [{"type": "RMSE"}, {"type": "MAE"}, {"type": "Max"}],
}

# Create pipeline config
pipeline_config = {
    "elements": {
        "entry": {**entry_config, "element_type": "entry"},
        "validate": {**validation_config, "element_type": "validation"},
    },
}

# Submit pipeline
pipeline = client.pipeline.create(pipeline_config)

# Get job results
while True:
    pipeline = client.pipeline.get(pipeline.id)
    print(f"Pipeline {pipeline.id} status: {pipeline.status}")
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print("Pipeline completed")
        print("Summary Stats:", result.element_results["validate"]["summary_stats"])
        break
    elif pipeline.status == "failed":
        print("Pipeline failed")
        print("Error:", pipeline.error)
        break
    time.sleep(1)
