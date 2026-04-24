"""
Example: Create a protocol-based simulation using an existing parameterized model ID.

This example shows how to use a parameterized model ID string instead of a quick
model or full model dictionary. You would typically get the parameterized model ID
from creating a model first or from a previous simulation. It polls for completion
and plots the results.

Set the TEST_PARAMETERIZED_MODEL_ID environment variable to specify which
parameterized model ID to use.
"""

import os

from examples.simulation.utils import plot_simulation_results
from ionworks import Ionworks

# Initialize client
client = Ionworks()

# Define a simple protocol
protocol_yaml = """global:
  initial_state_type: soc_percentage
  initial_state_value: input['Initial SOC [%]']
  initial_temperature: 25.0
steps:
  - Charge:
      mode: C-rate
      value: "1"
      ends:
        - Voltage > 4.2
  - Rest:
      duration: 3600
"""

# Create simulation with parameterized model ID
# Get parameterized model ID from environment variable
parameterized_model_id = os.getenv("TEST_PARAMETERIZED_MODEL_ID")
if not parameterized_model_id:
    print("Error: TEST_PARAMETERIZED_MODEL_ID environment variable is required")
    print("Set it with: export TEST_PARAMETERIZED_MODEL_ID=your-parameterized-model-id")
    exit(1)
config = {
    "parameterized_model": parameterized_model_id,
    "protocol_experiment": {
        "protocol": protocol_yaml,
        "name": "Protocol with Parameterized Model ID",
    },
    "experiment_parameters": {"Initial SOC [%]": 50},
    "max_backward_jumps": 5,
}

print(
    "Creating protocol-based simulation with parameterized model ID: "
    f"'{parameterized_model_id}'..."
)
try:
    result = client.simulation.protocol(config)
    print("Simulation created!")
    print(f"  Simulation ID: {result.simulation_id}")
    print(f"  Job ID: {result.job_id}")
except Exception as e:
    print(f"Error: {e}")
    print(
        f"Note: Make sure parameterized model ID '{parameterized_model_id}' "
        "exists in your system, or set TEST_PARAMETERIZED_MODEL_ID environment "
        "variable to a valid parameterized model ID."
    )
    exit(1)

# Wait for completion (polls automatically)
simulation = client.simulation.wait_for_completion(
    result.simulation_id, timeout=60, poll_interval=2
)

# Get simulation results
simulation_data = client.simulation.get_result(result.simulation_id)
time_series = simulation_data.get("time_series", {})

if not time_series:
    print("No time series data available")
    exit(1)

# Plot results using utility function
plot_simulation_results(time_series, title="Simulation Results")
