"""
Example: Create a single protocol-based simulation.

This example demonstrates how to create a simulation using the Universal
Cycler Protocol (UCP) format with a quick model configuration, poll for
completion, and plot the results.
"""

from examples.simulation.utils import plot_simulation_results
from ionworks import Ionworks

# Initialize client
client = Ionworks()

# Define a simple charge/discharge protocol in YAML format
protocol_yaml = """global:
  initial_state_type: soc_percentage
  initial_state_value: 50
  initial_temperature: 25.0
  resolution:
    time: 1.0
    voltage: 0.001
    current: 0.001
steps:
  - Charge:
      mode: C-rate
      value: "0.6"
      ends:
        - Voltage > 4.2
  - Rest:
      duration: 3600
  - Discharge:
      mode: C-rate
      value: "0.5"
      ends:
        - Voltage < 2.5
"""

# Create simulation with quick model
config = {
    "parameterized_model": {
        "quick_model": {"capacity": 1.0, "chemistry": "NMC/Graphite"}
    },
    "protocol_experiment": {
        "protocol": protocol_yaml,
        "name": "NMC Charge Discharge Protocol",
    },
}

print("Creating protocol-based simulation...")
result = client.simulation.protocol(config)

print("Simulation created!")
print(f"  Simulation ID: {result.simulation_id}")
print(f"  Job ID: {result.job_id}")

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
