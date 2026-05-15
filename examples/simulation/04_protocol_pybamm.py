"""
Example: Create a protocol-based simulation using PyBaMM format.

This example demonstrates how to create a simulation using a PyBaMM-style
protocol string instead of the Universal Cycler Protocol (UCP) YAML format.
The API automatically converts PyBaMM format strings to UCP format.
"""

from examples.simulation.utils import plot_simulation_results
from ionworks import Ionworks

# Initialize client
client = Ionworks()

# Define a protocol in PyBaMM string format
# PyBaMM format uses natural language descriptions of cycling steps
protocol_pybamm = """
Discharge at 0.5C until 2.5V
Charge at 0.5C until 4.2V
Hold at 4.2V until C/50
"""

# Create simulation with quick model
config = {
    "parameterized_model": {
        "quick_model": {"capacity": 1.0, "chemistry": "NMC/Graphite"}
    },
    "protocol_experiment": {
        "protocol": protocol_pybamm,
        "name": "PyBaMM Charge Discharge Protocol",
    },
}

print("Creating protocol-based simulation with PyBaMM format...")
print(f"Protocol string: {protocol_pybamm}")
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
plot_simulation_results(time_series, title="Simulation Results (PyBaMM Protocol)")
