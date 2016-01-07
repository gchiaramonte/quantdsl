from collections import defaultdict
from threading import Thread

from quantdsl.domain.model.call_dependencies import  CallDependenciesRepository, register_call_dependencies
from quantdsl.domain.model.call_dependents import CallDependentsRepository, register_call_dependents
from quantdsl.domain.model.call_leafs import register_call_leafs
from quantdsl.domain.model.call_link import register_call_link
from quantdsl.domain.model.call_requirement import register_call_requirement
from quantdsl.domain.model.call_result import CallResultRepository, make_call_result_id
from quantdsl.domain.model.contract_specification import ContractSpecification
from quantdsl.semantics import Module, DslNamespace, extract_defs_and_exprs, DslExpression, generate_stubbed_calls
from quantdsl.domain.services.parser import dsl_parse


def generate_dependency_graph(contract_specification, call_dependencies_repo, call_dependents_repo, call_leafs_repo,
                              call_requirement_repo):

    assert isinstance(contract_specification, ContractSpecification)
    dsl_module = dsl_parse(dsl_source=contract_specification.specification)
    assert isinstance(dsl_module, Module)
    dsl_globals = DslNamespace()
    function_defs, expressions = extract_defs_and_exprs(dsl_module, dsl_globals)
    dsl_expr = expressions[0]
    assert isinstance(dsl_expr, DslExpression)
    dsl_locals = DslNamespace()

    leaf_ids = []
    all_dependents = defaultdict(list)

    # Generate stubbed call from the parsed DSL module object.
    for stub in generate_stubbed_calls(contract_specification.id, dsl_module, dsl_expr, dsl_globals, dsl_locals):
        # assert isinstance(stub, StubbedCall)

        # Register the call requirements.
        call_id = stub.call_id
        dsl_source = str(stub.dsl_expr)
        effective_present_time = stub.effective_present_time
        call_requirement = register_call_requirement(call_id, dsl_source, effective_present_time)

        # # Hold onto the dsl_expr, if single threaded... this really isn't useful for distributed modes.
        call_requirement._dsl_expr = stub.dsl_expr
        call_requirement_repo.add_cache(call_id, call_requirement)

        # Register the call dependencies.
        dependencies = stub.dependencies
        register_call_dependencies(call_id, dependencies)

        # Keep track of the leaves and the dependents.
        if len(dependencies) == 0:
            leaf_ids.append(call_id)
        else:
            for dependency_call_id in dependencies:
                all_dependents[dependency_call_id].append(call_id)

    # Register the call dependents.
    for call_id, dependents in all_dependents.items():
        register_call_dependents(call_id, dependents)
    register_call_dependents(contract_specification.id, [])

    # Generate and register the call order.
    link_id = contract_specification.id
    for call_id in generate_execution_order(leaf_ids, call_dependents_repo, call_dependencies_repo):
        register_call_link(link_id, call_id)
        link_id = call_id

    # Register the leaf ids.
    register_call_leafs(contract_specification.id, leaf_ids)


def get_dependency_values(contract_valuation_id, call_id, perturbed_market_name, dependencies_repo, result_repo):
    assert isinstance(result_repo, CallResultRepository), result_repo
    dependency_values = {}
    stub_dependencies = dependencies_repo[call_id]
    # assert isinstance(stub_dependencies, CallDependencies), stub_dependencies

    is_threaded = False
    threads = []
    for stub_id in stub_dependencies:
        if is_threaded:
            t = Thread(target=get_dependency_value,
                       args=(contract_valuation_id, stub_id, perturbed_market_name, result_repo, dependency_values))
            t.start()
            threads.append(t)
        else:
            get_dependency_value(contract_valuation_id, stub_id, perturbed_market_name, result_repo, dependency_values)
    [t.join() for t in threads]
    return dependency_values


def get_dependency_value(contract_valuation_id, stub_id, perturbed_market_name, result_repo, dependency_values):
    call_result_id = make_call_result_id(contract_valuation_id, stub_id, perturbed_market_name)
    try:
        stub_result = result_repo[call_result_id]
    except KeyError:
        raise
    else:
        # assert isinstance(stub_result, CallResult), stub_result
        value = stub_result.result_value
        dependency_values[stub_id] = value


def generate_execution_order(leaf_call_ids, call_dependents_repo, call_dependencies_repo):
    assert isinstance(call_dependents_repo, CallDependentsRepository)
    assert isinstance(call_dependencies_repo, CallDependenciesRepository)

    # Topological sort, using Kahn's algorithm.

    # Initialise set of nodes that have no outstanding dependencies with the leaf nodes.
    S = set(leaf_call_ids)
    removed_edges = defaultdict(set)
    while S:

        # Pick a node, n, that has zero outstanding dependencies.
        n = S.pop()

        # Yield node n.
        yield n

        # Get dependents, if any were registered.
        try:
            dependents = call_dependents_repo[n]
        except KeyError:
            continue

        # Visit the nodes that are dependent on n.
        for m in dependents:

            # Remove the edge n to m from the graph.
            removed_edges[m].add(n)

            # If there are zero edges to m that have not been removed, then we
            # can add m to the set of nodes with zero outstanding dependencies.
            for d in call_dependencies_repo[m]:
                if d not in removed_edges[m]:
                    break
            else:
                # Forget about removed edges to m.
                removed_edges.pop(m)

                # Add m to the set of nodes that have zero outstanding dependencies.
                S.add(m)
