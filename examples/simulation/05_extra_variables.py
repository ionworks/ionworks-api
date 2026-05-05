"""
Example: Create a protocol-based simulation with extra variables.

This example demonstrates how to use the extra_variables functionality to
include additional variables in the simulation output. Specifically, it plots
the X-averaged negative electrode surface potential difference at separator
interface [V] along with standard voltage and current plots.
"""

from examples.simulation.utils import plot_simulation_results
from ionworks import Ionworks
import matplotlib.pyplot as plt
import pandas as pd

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
      value: "0.61"
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

# Create simulation with quick model and extra variables
extra_var_name = (
    "Negative electrode surface potential difference at separator interface [V]"
)
config = {
    "parameterized_model": {
        "quick_model": {"capacity": 1.0, "chemistry": "NMC/Graphite"}
    },
    "protocol_experiment": {
        "protocol": protocol_yaml,
        "name": "NMC Charge Discharge Protocol with Extra Variables",
    },
    "extra_variables": [extra_var_name],
}

print("Creating protocol-based simulation with extra variables...")
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

# Plot standard results
plot_simulation_results(time_series, title="Simulation Results with Extra Variables")

# Plot the extra variable

df = pd.DataFrame(time_series)
if extra_var_name in df.columns:
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df["Time [s]"], df[extra_var_name])
    ax.set_xlabel("Time [s]")
    ax.set_ylabel(extra_var_name)
    ax.set_title("X-averaged Negative Electrode Surface Potential Difference")
    ax.grid(True)
    plt.tight_layout()
    plt.show()
    print(f"\nPlotted extra variable: {extra_var_name}")
else:
    print(f"\nWarning: Extra variable '{extra_var_name}' not found in results")
    print(f"Available columns: {list(df.columns)}")
