"""
Ionworks API Client.

A Python client for interacting with the Ionworks platform for battery cell
testing, simulation, and modeling.

Example:
-------
>>> from ionworks import Ionworks
>>> client = Ionworks()
>>> specs = client.cell_spec.list()
"""

from .cache import (
    clear_cache,
    get_cache_directory,
    get_cache_enabled,
    get_cache_ttl,
    set_cache_directory,
    set_cache_enabled,
    set_cache_ttl,
)
from .client import Ionworks
from .ecm import EcmFitJob, FitResults, RcPairFit, SaveToProjectResponse
from .electrolyte import ElectrolyteClient
from .errors import IonworksError
from .models import (
    KNOWN_ANALYSIS_TYPES,
    Analysis,
    AnalysisColumnSpec,
    AnalysisType,
    Channel,
    ChannelState,
    ColumnSpec,
    ComputeUsage,
    Cycler,
    CyclerDetail,
    FlatChannel,
    LabChannel,
    LabCycler,
    LabMeasurementSummary,
    LabSite,
    LabStatus,
    Material,
    MaterialPropertyDataset,
    MeasurementType,
    Model,
    Optimization,
    OrganizationUsage,
    PaginatedList,
    ParameterizedModel,
    Project,
    RawData,
    SimulationUsage,
    Site,
    SiteDetail,
    Study,
    Utilization,
)
from .navigator import Navigator
from .simulation import SimulationResult
from .validators import (
    IssueCode,
    MeasurementValidationError,
    ValidationIssue,
    get_dataframe_backend,
    set_dataframe_backend,
)

__all__ = [
    "KNOWN_ANALYSIS_TYPES",
    "Analysis",
    "AnalysisColumnSpec",
    "AnalysisType",
    "Channel",
    "ChannelState",
    "ColumnSpec",
    "ComputeUsage",
    "Cycler",
    "CyclerDetail",
    "EcmFitJob",
    "ElectrolyteClient",
    "FitResults",
    "FlatChannel",
    "Ionworks",
    "IonworksError",
    "LabChannel",
    "LabCycler",
    "LabMeasurementSummary",
    "LabSite",
    "LabStatus",
    "Material",
    "MaterialPropertyDataset",
    "IssueCode",
    "MeasurementType",
    "MeasurementValidationError",
    "Model",
    "Navigator",
    "Optimization",
    "OrganizationUsage",
    "PaginatedList",
    "ParameterizedModel",
    "Project",
    "RawData",
    "RcPairFit",
    "SaveToProjectResponse",
    "SimulationResult",
    "SimulationUsage",
    "Site",
    "SiteDetail",
    "Study",
    "Utilization",
    "ValidationIssue",
    "clear_cache",
    "get_cache_directory",
    "get_cache_enabled",
    "get_cache_ttl",
    "get_dataframe_backend",
    "set_cache_directory",
    "set_cache_enabled",
    "set_cache_ttl",
    "set_dataframe_backend",
]
