"""Build concentration-dependent electrolyte transport parameters from data.

A material property dataset in the Ionworks database stores electrolyte
transport properties (conductivity, diffusivity, cation transference number,
thermodynamic factor) as a function of electrolyte concentration.
:class:`ElectrolyteClient` turns such a dataset into a
:class:`pybamm.ParameterValues` of concentration-dependent functions, ready to
drop into a pipeline ``DirectEntry``.

Each property can be represented in one of two ways:

- ``"interpolant"`` — a tabulated :class:`pybamm.Interpolant` of the measured
  points (linear extrapolation).
- ``"landesfeind"`` — the Landesfeind & Gasteiger (2019) functional form for
  that property, fitted to the measured points. The dataset is treated as
  isothermal (single temperature), so each T-dependent Landesfeind form
  collapses to its isothermal reduction:

  - conductivity ``[S.m-1]`` → ``q1 * c * (1 + q2*sqrt(c) + q3*c) /
    (1 + q4*c**4) / 10`` (nonlinear least squares),
  - diffusivity ``[m2.s-1]`` → ``A * exp(B * c)`` (log-linear least squares),
  - cation transference number / thermodynamic factor → cubic polynomial in
    ``c`` (linear least squares),

  with ``c`` in mol.L-1. Unlike a tabulated interpolant, the fitted conductivity
  and diffusivity forms stay positive and finite below the lowest measured
  concentration, which keeps high-rate DFN solves stable when the electrolyte
  depletes near an electrode.

These forms mirror ``ionworkspipeline.direct_entries.landesfeind_electrolyte``
(which carries the full T-dependent forms with literature coefficients); here
the coefficients are fitted to a customer dataset instead.

The returned functions take exactly ``(c_e, T)`` and rebind to the model's own
``c_e`` once serialised through ``DirectEntry``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy.optimize import curve_fit

if TYPE_CHECKING:
    import pybamm

# Default pybamm parameter name -> dataset column name.
DEFAULT_COLUMNS: dict[str, str] = {
    "Electrolyte conductivity [S.m-1]": "Electrolyte conductivity",
    "Electrolyte diffusivity [m2.s-1]": "Electrolyte diffusivity",
    "Cation transference number": "Transference number",
}

DEFAULT_CONCENTRATION_COLUMN = "Electrolyte concentration"

# Dataset/model concentration convention (mol.m-3) -> mol.L-1, the unit the
# Landesfeind forms are expressed in.
_M3_TO_LITRE = 1.0e-3


def _interpolant_form(
    c_e: np.ndarray, y: np.ndarray, name: str
) -> Callable[[Any, Any], Any]:
    """Tabulated interpolant of the measured points."""
    import pybamm

    return lambda c_e_sym, T: pybamm.Interpolant(c_e, y, c_e_sym, name=name)


def _landesfeind_conductivity(
    c_e: np.ndarray, y: np.ndarray, name: str
) -> Callable[[Any, Any], Any]:
    """Isothermal Landesfeind conductivity, fitted by nonlinear least squares.

    ``sigma(c) = q1 * c * (1 + q2*sqrt(c) + q3*c) / (1 + q4*c**4) / 10`` with
    ``c`` in mol.L-1 and ``sigma`` in S.m-1 (the ``/10`` converts mS.cm-1).
    """
    import pybamm

    c = c_e * _M3_TO_LITRE

    def model(c_l, q1, q2, q3, q4):
        return q1 * c_l * (1 + q2 * np.sqrt(c_l) + q3 * c_l) / (1 + q4 * c_l**4) / 10

    popt, _ = curve_fit(model, c, y, p0=[10.0, 0.0, 0.0, 0.0], maxfev=100000)
    q1, q2, q3, q4 = (float(p) for p in popt)

    def func(c_e_sym, T):
        c_l = c_e_sym * _M3_TO_LITRE
        return (
            q1 * c_l * (1 + q2 * pybamm.sqrt(c_l) + q3 * c_l) / (1 + q4 * c_l**4) / 10
        )

    return func


def _landesfeind_diffusivity(
    c_e: np.ndarray, y: np.ndarray, name: str
) -> Callable[[Any, Any], Any]:
    """Isothermal Landesfeind diffusivity ``D = A * exp(B * c)``.

    Fitted by linear least squares of ``ln D`` against concentration in mol.L-1.
    """
    import pybamm

    c = c_e * _M3_TO_LITRE
    slope, intercept = np.polyfit(c, np.log(y), 1)
    a_coeff, b_coeff = float(np.exp(intercept)), float(slope)
    return lambda c_e_sym, T: a_coeff * pybamm.exp(b_coeff * c_e_sym * _M3_TO_LITRE)


def _landesfeind_polynomial(
    c_e: np.ndarray, y: np.ndarray, name: str
) -> Callable[[Any, Any], Any]:
    """Isothermal Landesfeind transference / thermodynamic factor.

    A cubic polynomial in concentration (mol.L-1), fitted by linear least
    squares.
    """
    c = c_e * _M3_TO_LITRE
    coeffs = [float(x) for x in np.polyfit(c, y, 3)]  # highest power first

    def func(c_e_sym, T):
        c_l = c_e_sym * _M3_TO_LITRE
        result = coeffs[0]
        for coeff in coeffs[1:]:
            result = result * c_l + coeff
        return result

    return func


# pybamm parameter name -> its Landesfeind isothermal fitter.
_LANDESFEIND_FITTERS: dict[
    str, Callable[[np.ndarray, np.ndarray, str], Callable[[Any, Any], Any]]
] = {
    "Electrolyte conductivity [S.m-1]": _landesfeind_conductivity,
    "Electrolyte diffusivity [m2.s-1]": _landesfeind_diffusivity,
    "Cation transference number": _landesfeind_polynomial,
    "Thermodynamic factor": _landesfeind_polynomial,
}


def _build_form(
    form: str, param: str, c_e: np.ndarray, y: np.ndarray
) -> Callable[[Any, Any], Any]:
    if form == "interpolant":
        return _interpolant_form(c_e, y, param)
    if form == "landesfeind":
        if param not in _LANDESFEIND_FITTERS:
            raise ValueError(
                f"No Landesfeind form for {param!r}; "
                f"available: {sorted(_LANDESFEIND_FITTERS)}."
            )
        return _LANDESFEIND_FITTERS[param](c_e, y, param)
    raise ValueError(
        f"Unknown form {form!r} for {param!r}; choose 'interpolant' or 'landesfeind'."
    )


class ElectrolyteClient:
    """Build electrolyte transport parameters from a material property dataset.

    Access via ``client.electrolyte``.
    """

    def __init__(self, client: Any) -> None:
        """Initialise the ElectrolyteClient.

        Parameters
        ----------
        client : Any
            The parent :class:`~ionworks.client.Ionworks` instance.
        """
        self.client = client

    def transport_from_dataset(
        self,
        dataset_id: str,
        forms: Mapping[str, str] | None = None,
        columns: Mapping[str, str] | None = None,
        concentration_column: str = DEFAULT_CONCENTRATION_COLUMN,
    ) -> pybamm.ParameterValues:
        """Build concentration-dependent electrolyte transport parameters.

        Downloads the material property dataset, then turns each requested
        column into a concentration-dependent function under its pybamm
        parameter name.

        Parameters
        ----------
        dataset_id : str
            UUID of the material property dataset holding the transport
            properties versus electrolyte concentration.
        forms : Mapping[str, str] | None, optional
            Per-parameter representation, keyed by pybamm parameter name. Each
            value is one of ``"interpolant"`` (default) or ``"landesfeind"``
            (the fitted isothermal Landesfeind form for that property).
            Parameters omitted here default to ``"interpolant"``.
        columns : Mapping[str, str] | None, optional
            Mapping of pybamm parameter name to dataset column name. Defaults
            to :data:`DEFAULT_COLUMNS` (conductivity, diffusivity, cation
            transference number).
        concentration_column : str, optional
            Name of the electrolyte-concentration column (mol.m-3) in the
            dataset. Defaults to :data:`DEFAULT_CONCENTRATION_COLUMN`.

        Returns
        -------
        pybamm.ParameterValues
            Parameter values mapping each pybamm parameter name to a
            concentration-dependent function ``(c_e, T)``. Pass straight to
            ``ionworks_schema.direct_entries.DirectEntry(parameters=...)``.

        Examples
        --------
        >>> from ionworks import Ionworks
        >>> import ionworks_schema as iws
        >>> client = Ionworks()
        >>> params = client.electrolyte.transport_from_dataset(
        ...     "dataset-uuid",
        ...     forms={"Electrolyte diffusivity [m2.s-1]": "landesfeind"},
        ... )
        >>> entry = iws.direct_entries.DirectEntry(parameters=params)
        """
        import pybamm

        columns = columns or DEFAULT_COLUMNS
        forms = forms or {}

        unmapped = set(forms) - set(columns)
        if unmapped:
            raise ValueError(
                f"forms specified for parameters not in columns: {sorted(unmapped)}. "
                f"Every form key must be one of the columns being built: "
                f"{sorted(columns)}."
            )

        df = self.client.material_property_dataset.get_data(dataset_id)
        c_e = df[concentration_column].to_numpy()

        funcs: dict[str, Callable[[Any, Any], Any]] = {}
        for param, column in columns.items():
            y = df[column].to_numpy()
            form = forms.get(param, "interpolant")
            funcs[param] = _build_form(form, param, c_e, y)

        return pybamm.ParameterValues(funcs)
