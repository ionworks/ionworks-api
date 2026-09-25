import time

from ionworks import Ionworks

# Setup Ionworks client and pipeline
client = Ionworks()

# Perform entry
entry_config = {"values": {"a": 1, "b": 2}}
pipeline_config = {
    "elements": {
        "entry": {**entry_config, "element_type": "entry"},
    },
}

pipeline = client.pipeline.create(pipeline_config)
print(f"Pipeline ID: {pipeline.id}")

# Wait for completion

while True:
    pipeline = client.pipeline.get(pipeline.id)
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print("Parameter Values:", result.element_results["entry"]["parameter_values"])
        break
    elif pipeline.status == "failed":
        print("Pipeline failed:", pipeline.error)
        break
    time.sleep(1)
