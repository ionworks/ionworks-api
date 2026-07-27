"""
Example: Create batch protocol-based simulations with Design of experiments.

This example demonstrates how to create multiple simulations using DOE
(Design of experiments) to explore different parameter combinations, poll
for completion, and plot the results.
"""

from examples.simulation.utils import plot_batch_simulation_results
from ionworks import Ionworks

# Initialize client
client = Ionworks()

# Define a charge/discharge protocol
protocol_yaml = """global:
  initial_state_type: soc_percentage
  initial_state_value: 50
  initial_temperature: 25.0
steps:
  - Charge:
      mode: C-rate
      value: "0.5"
      ends:
        - Voltage > 4.2
  - Rest:
      duration: 1800
  - Discharge:
      mode: C-rate
      value: "0.5"
      ends:
        - Voltage < 2.5
"""

# Create batch simulation with DOE
# This will create 3 x 2 = 6 simulations (grid sampling)
config = {
    "parameterized_model": {
        "quick_model": {"capacity": 1.0, "chemistry": "NMC/Graphite"}
    },
    "protocol_experiment": {
        "protocol": protocol_yaml,
        "name": "Batch Charge Discharge Protocol",
    },
    "design_parameters_doe": {
        "sampling": "grid",
        "rows": [
            {
                "type": "range",
                "name": "Positive electrode active material volume fraction",
                "min": 0.6,
                "max": 0.7,
                "count": 3,  # Creates 3 values: 0.6, 0.65, 0.7
            },
            {
                "type": "discrete",
                "name": "Positive electrode thickness [m]",
                "values": [50e-6, 60e-6],  # Creates 2 values
            },
        ],
    },
}

print("Creating batch protocol-based simulations with DOE...")
results = client.simulation.protocol_batch(config)

print(f"\nCreated {len(results)} simulations:")
for i, result in enumerate(results, 1):
    print(f"  Simulation {i}:")
    print(f"    Simulation ID: {result.simulation_id}")
    print(f"    Job ID: {result.job_id}")

# Wait for completion (polls automatically)
simulation_ids = [r.simulation_id for r in results]
completed_sims = client.simulation.wait_for_completion(
    simulation_ids, timeout=60, poll_interval=2
)

# Extract IDs from completed simulations
completed_simulation_ids = [sim["id"] for sim in completed_sims]

# Plot results for completed simulations
if completed_simulation_ids:
    # Collect simulation data and metadata
    simulation_data_list = []
    simulation_metadata_list = []

    for sim_id in completed_simulation_ids:
        try:
            simulation_data = client.simulation.get_result(sim_id)
            simulation = client.simulation.get(sim_id)
            simulation_data_list.append(simulation_data)
            simulation_metadata_list.append(simulation)
        except Exception as e:
            print(f"Error getting simulation {sim_id}: {e}")
            continue

    # Plot using utility function
    plot_batch_simulation_results(
        simulation_data_list, simulation_metadata_list, title="Batch Simulation Results"
    )
