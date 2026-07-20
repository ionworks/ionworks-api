import time

from ionworks import Ionworks

# Setup Ionworks client and pipeline
client = Ionworks()

# Provide parameters through an entry element first
entry_config = {
    "values": {
        "Maximum concentration in negative electrode [mol.m-3]": 3600 / 96485,
        "Negative electrode loading [A.h.cm-2]": 2,
        "Negative electrode thickness [m]": 0.3,
    }
}

# Define calculation configuration
calculation_config = {
    "calculation": "ElectrodeVolumeFractionFromLoading",
    "electrode": "negative",
    "method": "loading",
}

# Create pipeline config
pipeline_config = {
    "elements": {
        "entry": {**entry_config, "element_type": "entry"},
        "calculation": {**calculation_config, "element_type": "calculation"},
    },
}

# Submit pipeline
pipeline = client.pipeline.create(pipeline_config)
print(f"Pipeline ID: {pipeline.id}")

# Wait for completion
while True:
    pipeline = client.pipeline.get(pipeline.id)
    if pipeline.status == "completed":
        result = client.pipeline.result(pipeline.id)
        print(
            "Parameter Values:",
            result.element_results["calculation"]["parameter_values"],
        )
        break
    elif pipeline.status == "failed":
        print("Pipeline failed:", pipeline.error)
        break
    time.sleep(1)
