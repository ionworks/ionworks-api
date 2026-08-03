"""
Utility functions for simulation examples.

Common plotting and data processing functions used across simulation examples.
"""

import matplotlib.pyplot as plt
import pandas as pd


def plot_simulation_results(
    time_series, title: str = "Simulation Results", verbose: bool = True
) -> None:
    """Plot simulation results in a 2x2 subplot layout.

    Parameters
    ----------
    time_series : DataFrame or dict
        Time-series data as a polars DataFrame, pandas DataFrame, or dict of
        lists with keys like ``"Time [s]"``, ``"Voltage [V]"``, ``"Current [A]"``.
    title : str, optional
        Title for the figure (default: "Simulation Results").
    verbose : bool, optional
        Whether to print debug information (default: True).
    """
    # Normalise to a pandas DataFrame for matplotlib compatibility
    if isinstance(time_series, pd.DataFrame):
        df = time_series
    elif isinstance(time_series, dict):
        df = pd.DataFrame(time_series)
    else:
        # Polars DataFrame (or any object with to_pandas())
        df = time_series.to_pandas()

    # Use the correct column names with units
    time_col = "Time [s]"
    voltage_col = "Voltage [V]"
    current_col = "Current [A]"

    # Check available columns
    if verbose:
        print(f"\nAvailable columns: {list(df.columns)}")
        print(f"Data shape: {df.shape}")

    # Plot results
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle(title, fontsize=14)

    # Voltage vs Time
    if time_col in df.columns and voltage_col in df.columns:
        axes[0, 0].plot(df[time_col], df[voltage_col])
        axes[0, 0].set_xlabel("Time [s]")
        axes[0, 0].set_ylabel("Voltage [V]")
        axes[0, 0].set_title("Voltage vs Time")
        axes[0, 0].grid(True)
    else:
        axes[0, 0].text(0.5, 0.5, "Voltage data not available", ha="center")
        axes[0, 0].set_title("Voltage vs Time (No Data)")

    # Current vs Time
    if time_col in df.columns and current_col in df.columns:
        axes[0, 1].plot(df[time_col], df[current_col])
        axes[0, 1].set_xlabel("Time [s]")
        axes[0, 1].set_ylabel("Current [A]")
        axes[0, 1].set_title("Current vs Time")
        axes[0, 1].grid(True)
    else:
        axes[0, 1].text(0.5, 0.5, "Current data not available", ha="center")
        axes[0, 1].set_title("Current vs Time (No Data)")

    # Check for SOC column (might be named differently)
    soc_col = None
    for col in df.columns:
        if "soc" in col.lower() or "stoichiometry" in col.lower():
            soc_col = col
            break

    capacity_col = "Capacity [A.h]"

    # SOC vs Time (or stoichiometry)
    if time_col in df.columns and soc_col:
        axes[1, 0].plot(df[time_col], df[soc_col])
        axes[1, 0].set_xlabel("Time [s]")
        axes[1, 0].set_ylabel(soc_col)
        axes[1, 0].set_title(f"{soc_col} vs Time")
        axes[1, 0].grid(True)
    # Plot capacity instead if available
    elif time_col in df.columns and capacity_col in df.columns:
        axes[1, 0].plot(df[time_col], df[capacity_col])
        axes[1, 0].set_xlabel("Time [s]")
        axes[1, 0].set_ylabel("Capacity [A.h]")
        axes[1, 0].set_title("Capacity vs Time")
        axes[1, 0].grid(True)
    else:
        axes[1, 0].text(0.5, 0.5, "SOC/Capacity data not available", ha="center")
        axes[1, 0].set_title("SOC/Capacity vs Time (No Data)")

    # Voltage vs SOC (or capacity)
    if soc_col and voltage_col in df.columns:
        axes[1, 1].plot(df[soc_col], df[voltage_col])
        axes[1, 1].set_xlabel(soc_col)
        axes[1, 1].set_ylabel("Voltage [V]")
        axes[1, 1].set_title("Voltage vs SOC")
        axes[1, 1].grid(True)
    elif capacity_col in df.columns and voltage_col in df.columns:
        axes[1, 1].plot(df[capacity_col], df[voltage_col])
        axes[1, 1].set_xlabel("Capacity [A.h]")
        axes[1, 1].set_ylabel("Voltage [V]")
        axes[1, 1].set_title("Voltage vs Capacity")
        axes[1, 1].grid(True)
    else:
        axes[1, 1].text(0.5, 0.5, "Data not available", ha="center")
        axes[1, 1].set_title("Voltage vs SOC/Capacity (No Data)")

    plt.tight_layout()
    plt.show()

    if verbose:
        print("\nPlot displayed successfully!")


def plot_batch_simulation_results(
    simulation_data_list: list,
    simulation_metadata_list: list[dict],
    title: str = "Batch Simulation Results",
) -> None:
    """Plot multiple simulation results on the same axes with labels.

    Parameters
    ----------
    simulation_data_list : list
        List of ``SimulationResult`` objects returned by
        ``client.simulation.get_result()``.
    simulation_metadata_list : list[dict]
        List of simulation metadata dictionaries, each containing
        ``"design_parameters"`` and ``"id"`` keys.
    title : str, optional
        Title for the figure (default: "Batch Simulation Results").
    """
    fig, axes = plt.subplots(2, 1, figsize=(10, 8))
    fig.suptitle(title, fontsize=14)

    # Use the correct column names with units
    time_col = "Time [s]"
    voltage_col = "Voltage [V]"
    current_col = "Current [A]"

    if len(simulation_data_list) != len(simulation_metadata_list):
        raise ValueError(
            f"simulation_data_list ({len(simulation_data_list)} items) and "
            f"simulation_metadata_list ({len(simulation_metadata_list)} items) "
            "must have the same length"
        )

    for sim_data, sim_metadata in zip(
        simulation_data_list, simulation_metadata_list, strict=True
    ):
        try:
            ts = sim_data.time_series
            if ts is None or len(ts) == 0:
                continue
            if isinstance(ts, pd.DataFrame):
                df = ts
            else:
                df = ts.to_pandas()
            design_params = sim_metadata.get("design_parameters", {})
            sim_id = sim_metadata.get("id", "")

            # Create label from design parameters
            label_parts = []
            for key, value in design_params.items():
                if "volume fraction" in key:
                    label_parts.append(f"ε={value:.3f}")
                elif "thickness" in key:
                    label_parts.append(f"L={value * 1e6:.1f}µm")
            label = ", ".join(label_parts) if label_parts else sim_id[:8]

            # Voltage vs Time
            if time_col in df.columns and voltage_col in df.columns:
                axes[0].plot(df[time_col], df[voltage_col], label=label, alpha=0.7)

            # Current vs Time
            if time_col in df.columns and current_col in df.columns:
                axes[1].plot(df[time_col], df[current_col], label=label, alpha=0.7)
        except Exception as e:
            print(f"Error plotting simulation: {e}")
            continue

    axes[0].set_xlabel("Time [s]")
    axes[0].set_ylabel("Voltage [V]")
    axes[0].set_title("Voltage vs Time")
    axes[0].legend()
    axes[0].grid(True)

    axes[1].set_xlabel("Time [s]")
    axes[1].set_ylabel("Current [A]")
    axes[1].set_title("Current vs Time")
    axes[1].legend()
    axes[1].grid(True)

    plt.tight_layout()
    plt.show()

    print(f"\nPlotted {len(simulation_data_list)} completed simulations!")
