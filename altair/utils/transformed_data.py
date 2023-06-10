from typing import List, Optional, Tuple, Dict, Iterable, overload, Union

import pandas as pd

from altair import (
    Chart,
    FacetChart,
    LayerChart,
    HConcatChart,
    VConcatChart,
    ConcatChart,
    data_transformers,
)
from altair.utils.schemapi import Undefined

Scope = Tuple[int, ...]
FacetMapping = Dict[Tuple[str, Scope], Tuple[str, Scope]]

MAGIC_CHART_NAME = "_vf_mark{}"


@overload
def transformed_data(
    chart: Union[Chart, FacetChart],
    row_limit: Optional[int] = None,
    exclude: Optional[Iterable[str]] = None,
) -> Optional[pd.DataFrame]:
    ...


@overload
def transformed_data(
    chart: Union[LayerChart, HConcatChart, VConcatChart, ConcatChart],
    row_limit: Optional[int] = None,
    exclude: Optional[Iterable[str]] = None,
) -> List[pd.DataFrame]:
    ...


def transformed_data(chart, row_limit=None, exclude=None):
    """Evaluate a Chart's transforms

    Evaluate the data transforms associated with a Chart and return the
    transformed data as one or more DataFrames

    Parameters
    ----------
    chart : Chart, FacetChart, LayerChart, HConcatChart, VConcatChart, or ConcatChart
        Altair chart to evaluate transforms on
    row_limit : int (optional)
        Maximum number of rows to return for each DataFrame. None (default) for unlimited
    exclude : iterable of str
        Set of the names of charts to exclude

    Returns
    -------
    DataFrame or list of DataFrame
        If input chart is a Chart or Facet Chart, returns a DataFrame of the transformed data
        Otherwise, returns a list of DataFrames of the transformed data
    """
    try:
        from vegafusion import runtime, get_local_tz, get_inline_datasets_for_spec  # type: ignore
    except ImportError as err:
        raise ImportError(
            "transformed_data requires the vegafusion-python-embed and vegafusion packages\n"
            "These can be installed with pip using:\n"
            "    pip install vegafusion[embed]\n"
            "Or with conda using:\n"
            "    conda install -c conda-forge vegafusion-python-embed vegafusion"
        ) from err

    if isinstance(chart, Chart):
        # Add dummy mark if None specified to satisfy Vega-Lite
        if chart.mark == Undefined:
            chart = chart.mark_point()

    # Deep copy chart so that we can rename marks without affecting caller
    chart = chart.copy(deep=True)

    # Rename chart or subcharts with magic names that we can look up in the
    # resulting Vega specification
    chart_names = name_chart(chart, 0, exclude=exclude)

    # Compile to Vega and extract inline DataFrames
    with data_transformers.enable("vegafusion-inline"):
        vega_spec = chart.to_dict(format="vega")
        inline_datasets = get_inline_datasets_for_spec(vega_spec)

    # Build mapping from mark names to vega datasets
    facet_mapping = get_facet_mapping(vega_spec)
    dataset_mapping = get_datasets_for_chart_names(
        vega_spec, chart_names, facet_mapping
    )

    # Build a list of vega dataset names that corresponds to the order
    # of the chart components
    dataset_names = []
    for chart_name in chart_names:
        if chart_name in dataset_mapping:
            dataset_names.append(dataset_mapping[chart_name])
        else:
            raise ValueError("Failed to locate all datasets")

    # Extract transformed datasets with VegaFusion
    datasets, warnings = runtime.pre_transform_datasets(
        vega_spec,
        dataset_names,
        get_local_tz(),
        row_limit=row_limit,
        inline_datasets=inline_datasets,
    )

    if isinstance(chart, (Chart, FacetChart)):
        # Return DataFrame (or None if it was excluded) if input was a simple Chart
        if not datasets:
            return None
        else:
            return datasets[0]
    else:
        # Otherwise return the list of DataFrames
        return datasets


def make_magic_chart_name(i: int) -> str:
    """Make magic chart name for chart number i

    Parameters
    ----------
    i : int
        Mark number

    Returns
    -------
    str
        Mark name
    """
    return MAGIC_CHART_NAME.format(i)


def name_chart(
    chart: Union[
        Chart, FacetChart, LayerChart, HConcatChart, VConcatChart, ConcatChart
    ],
    i: int = 0,
    exclude: Optional[Iterable[str]] = None,
) -> List[str]:
    """Name unnamed charts and subcharts

    Name unnamed charts and subcharts so that we can look them up later in
    the compiled Vega spec.

    Note: This function mutates the input chart by applying names to
    unnamed charts.

    Parameters
    ----------
    chart : Chart, FacetChart, LayerChart, HConcatChart, VConcatChart, or ConcatChart
        Altair chart to apply names to
    i : int (default 0)
        Starting chart index
    exclude : iterable of str
        Names of charts to exclude

    Returns
    -------
    list of str
        List of the names of the charts and subcharts
    """
    exclude = set(exclude) if exclude is not None else set()
    if isinstance(chart, (Chart, FacetChart)):
        # Perform shallow copy of chart so that we can change
        # the name
        if chart.name not in exclude:
            if chart.name in (None, Undefined):
                name = make_magic_chart_name(i)
                chart.name = name
            return [chart.name]
        else:
            return []
    else:
        if isinstance(chart, LayerChart):
            subcharts = chart.layer
        elif isinstance(chart, HConcatChart):
            subcharts = chart.hconcat
        elif isinstance(chart, VConcatChart):
            subcharts = chart.vconcat
        elif isinstance(chart, ConcatChart):
            subcharts = chart.concat
        else:
            raise ValueError(
                "transformed_data accepts an instance of "
                "Chart, FacetChart, LayerChart, HConcatChart, VConcatChart, or ConcatChart\n"
                f"Received value of type: {type(chart)}"
            )

        chart_names: List[str] = []
        for subchart in subcharts:
            for name in name_chart(subchart, i=i + len(chart_names), exclude=exclude):
                chart_names.append(name)
        return chart_names


def get_group_mark_for_scope(vega_spec: dict, scope: Scope) -> Optional[dict]:
    """Get the group mark at a particular scope

    Parameters
    ----------
    vega_spec : dict
        Top-level Vega specification dictionary
    scope : tuple of int
        Scope tuple. If empty, the original Vega specification is returned.
        Otherwise, the nested group mark at the scope specified is returned.

    Returns
    -------
    dict or None
        Top-level Vega spec (if scope is empty)
        or group mark (if scope is non-empty)
        or None (if group mark at scope does not exist)

    Examples
    --------
    >>> spec = {
    ...     "marks": [
    ...         {
    ...             "type": "group",
    ...             "marks": [{"type": "symbol"}]
    ...         },
    ...         {
    ...             "type": "group",
    ...             "marks": [{"type": "rect"}]}
    ...     ]
    ... }
    >>> get_group_mark_for_scope(spec, (1,))
    {'type': 'group', 'marks': [{'type': 'rect'}]}
    """
    group = vega_spec

    # Find group at scope
    for scope_value in scope:
        group_index = 0
        child_group = None
        for mark in group.get("marks", []):
            if mark.get("type") == "group":
                if group_index == scope_value:
                    child_group = mark
                    break
                group_index += 1
        if child_group is None:
            return None
        group = child_group

    return group


def get_datasets_for_scope(vega_spec: dict, scope: Scope) -> List[str]:
    """Get the names of the datasets that are defined at a given scope

    Parameters
    ----------
    vega_spec : dict
        Top-leve Vega specification
    scope : tuple of int
        Scope tuple. If empty, the names of top-level datasets are returned
        Otherwise, the names of the datasets defined in the nested group mark
        at the specified scope are returned.

    Returns
    -------
    list of str
        List of the names of the datasets defined at the specified scope

    Examples
    --------
    >>> spec = {
    ...     "data": [
    ...         {"name": "data1"}
    ...     ],
    ...     "marks": [
    ...         {
    ...             "type": "group",
    ...             "data": [
    ...                 {"name": "data2"}
    ...             ],
    ...             "marks": [{"type": "symbol"}]
    ...         },
    ...         {
    ...             "type": "group",
    ...             "data": [
    ...                 {"name": "data3"},
    ...                 {"name": "data4"},
    ...             ],
    ...             "marks": [{"type": "rect"}]
    ...         }
    ...     ]
    ... }

    >>> get_datasets_for_scope(spec, ())
    ['data1']

    >>> get_datasets_for_scope(spec, (0,))
    ['data2']

    >>> get_datasets_for_scope(spec, (1,))
    ['data3', 'data4']

    Returns empty when no group mark exists at scope
    >>> get_datasets_for_scope(spec, (1, 3))
    []
    """
    group = get_group_mark_for_scope(vega_spec, scope) or {}

    # get datasets from group
    datasets = []
    for dataset in group.get("data", []):
        datasets.append(dataset["name"])

    # Add facet dataset
    facet_dataset = group.get("from", {}).get("facet", {}).get("name", None)
    if facet_dataset:
        datasets.append(facet_dataset)
    return datasets


def get_definition_scope_for_data_reference(
    vega_spec: dict, data_name: str, usage_scope: Scope
) -> Optional[Scope]:
    """Return the scope that a dataset is defined at, for a given usage scope

    Parameters
    ----------
    vega_spec: dict
        Top-level Vega specification
    data_name: str
        The name of a dataset reference
    usage_scope: tuple of int
        The scope that the dataset is referenced in

    Returns
    -------
    tuple of int
        The scope where the referenced dataset is defined,
        or None if no such dataset is found

    Examples
    --------
    >>> spec = {
    ...     "data": [
    ...         {"name": "data1"}
    ...     ],
    ...     "marks": [
    ...         {
    ...             "type": "group",
    ...             "data": [
    ...                 {"name": "data2"}
    ...             ],
    ...             "marks": [{
    ...                 "type": "symbol",
    ...                 "encode": {
    ...                     "update": {
    ...                         "x": {"field": "x", "data": "data1"},
    ...                         "y": {"field": "y", "data": "data2"},
    ...                     }
    ...                 }
    ...             }]
    ...         }
    ...     ]
    ... }

    data1 is referenced at scope [0] and defined at scope []
    >>> get_definition_scope_for_data_reference(spec, "data1", (0,))
    ()

    data2 is referenced at scope [0] and defined at scope [0]
    >>> get_definition_scope_for_data_reference(spec, "data2", (0,))
    (0,)

    If data2 is not visible at scope [] (the top level),
    because it's defined in scope [0]
    >>> repr(get_definition_scope_for_data_reference(spec, "data2", ()))
    'None'
    """
    for i in reversed(range(len(usage_scope) + 1)):
        scope = usage_scope[:i]
        datasets = get_datasets_for_scope(vega_spec, scope)
        if data_name in datasets:
            return scope
    return None


def get_facet_mapping(group: dict, scope: Scope = ()) -> FacetMapping:
    """Create mapping from facet definitions to source datasets

    Parameters
    ----------
    group : dict
        Top-level Vega spec or nested group mark
    scope : tuple of int
        Scope of the group dictionary within a top-level Vega spec

    Returns
    -------
    dict
        Dictionary from (facet_name, facet_scope) to (dataset_name, dataset_scope)

    Examples
    --------
    >>> spec = {
    ...     "data": [
    ...         {"name": "data1"}
    ...     ],
    ...     "marks": [
    ...         {
    ...             "type": "group",
    ...             "from": {
    ...                 "facet": {
    ...                     "name": "facet1",
    ...                     "data": "data1",
    ...                     "groupby": ["colA"]
    ...                 }
    ...             }
    ...         }
    ...     ]
    ... }
    >>> get_facet_mapping(spec)
    {('facet1', (0,)): ('data1', ())}
    """
    facet_mapping = {}
    group_index = 0
    mark_group = get_group_mark_for_scope(group, scope) or {}
    for mark in mark_group.get("marks", []):
        if mark.get("type", None) == "group":
            # Get facet for this group
            group_scope = scope + (group_index,)
            facet = mark.get("from", {}).get("facet", None)
            if facet is not None:
                facet_name = facet.get("name", None)
                facet_data = facet.get("data", None)
                if facet_name is not None and facet_data is not None:
                    definition_scope = get_definition_scope_for_data_reference(
                        group, facet_data, scope
                    )
                    if definition_scope is not None:
                        facet_mapping[(facet_name, group_scope)] = (
                            facet_data,
                            definition_scope,
                        )

            # Handle children recursively
            child_mapping = get_facet_mapping(group, scope=group_scope)
            facet_mapping.update(child_mapping)
            group_index += 1

    return facet_mapping


def get_from_facet_mapping(
    scoped_dataset: Tuple[str, Scope], facet_mapping: FacetMapping
) -> Tuple[str, Scope]:
    """Apply facet mapping to a scoped dataset

    Parameters
    ----------
    scoped_dataset : (str, tuple of int)
        A dataset name and scope tuple
    facet_mapping : dict from (str, tuple of int) to (str, tuple of int)
        The facet mapping produced by get_facet_mapping

    Returns
    -------
    (str, tuple of int)
        Dataset name and scope tuple that has been mapped as many times as possible

    Examples
    --------
    Facet mapping as produced by get_facet_mapping
    >>> facet_mapping = {("facet1", (0,)): ("data1", ()), ("facet2", (0, 1)): ("facet1", (0,))}
    >>> get_from_facet_mapping(("facet2", (0, 1)), facet_mapping)
    ('data1', ())
    """
    while scoped_dataset in facet_mapping:
        scoped_dataset = facet_mapping[scoped_dataset]
    return scoped_dataset


def get_datasets_for_chart_names(
    group: dict,
    vl_chart_names: List[str],
    facet_mapping: FacetMapping,
    scope: Scope = (),
) -> Dict[str, Tuple[str, Scope]]:
    """Get the Vega datasets that correspond to the provided Altair chart names

    Parameters
    ----------
    group : dict
        Top-level Vega spec or nested group mark
    vl_chart_names : list of str
        List of the Vega-Lite
    facet_mapping : dict from (str, tuple of int) to (str, tuple of int)
        The facet mapping produced by get_facet_mapping
    scope : tuple of int
        Scope of the group dictionary within a top-level Vega spec

    Returns
    -------
    dict from str to (str, tuple of int)
        Dict from Altair chart names to scoped datasets
    """
    datasets = {}
    group_index = 0
    mark_group = get_group_mark_for_scope(group, scope) or {}
    for mark in mark_group.get("marks", []):
        for vl_chart_name in vl_chart_names:
            if mark.get("name", "") == f"{vl_chart_name}_cell":
                data_name = mark.get("from", {}).get("facet", None).get("data", None)
                scoped_data_name = (data_name, scope)
                datasets[vl_chart_name] = get_from_facet_mapping(
                    scoped_data_name, facet_mapping
                )
                break

        name = mark.get("name", "")
        if mark.get("type", "") == "group":
            group_data_names = get_datasets_for_chart_names(
                group, vl_chart_names, facet_mapping, scope=scope + (group_index,)
            )
            for k, v in group_data_names.items():
                datasets.setdefault(k, v)
            group_index += 1
        else:
            for vl_chart_name in vl_chart_names:
                if name.startswith(vl_chart_name) and name.endswith("_marks"):
                    data_name = mark.get("from", {}).get("data", None)
                    scoped_data = get_definition_scope_for_data_reference(
                        group, data_name, scope
                    )
                    if scoped_data is not None:
                        datasets[vl_chart_name] = get_from_facet_mapping(
                            (data_name, scoped_data), facet_mapping
                        )
                        break

    return datasets
