include("run_opf.jl")
using PowerModelsSecurityConstrained

function contingency_label(contingency, position)
    for key in (:contingency_id, :id, :label)
        haskey(contingency, key) && return String(contingency[key])
    end
    return "contingency_$(position)"
end

function add_scopf_contingencies!(pm_data, case_data, contingencies)
    branch_index = Dict(String(branch[:branch_id]) => idx for (idx, branch) in enumerate(case_data[:branches]))
    gen_index = Dict(String(gen[:gen_id]) => idx for (idx, gen) in enumerate(case_data[:generators]))
    branch_contingencies = []
    gen_contingencies = []

    for (position, contingency) in enumerate(contingencies)
        event_type = haskey(contingency, :event_type) ? String(contingency[:event_type]) : "simultaneous"
        event_type == "simultaneous" || throw(ArgumentError(
            "PMSC coupled SCOPF supports static N-1 contingencies; got event_type=$(event_type)",
        ))
        components = haskey(contingency, :components) ? contingency[:components] : []
        length(components) == 1 || throw(ArgumentError(
            "PMSC coupled SCOPF requires exactly one outaged component per contingency; got $(length(components))",
        ))

        component = only(components)
        component_type = String(component[:type])
        component_id = String(component[:id])
        label = contingency_label(contingency, position)
        if component_type == "branch"
            haskey(branch_index, component_id) || throw(ArgumentError("unknown branch contingency id: $(component_id)"))
            push!(branch_contingencies, (idx=branch_index[component_id], label=label, type="branch"))
        elseif component_type == "generator"
            haskey(gen_index, component_id) || throw(ArgumentError("unknown generator contingency id: $(component_id)"))
            push!(gen_contingencies, (idx=gen_index[component_id], label=label, type="gen"))
        else
            throw(ArgumentError("unsupported contingency component type: $(component_type)"))
        end
    end

    pm_data["branch_contingencies"] = branch_contingencies
    pm_data["gen_contingencies"] = gen_contingencies
    return pm_data
end

function solve_scopf_request(case_data, payload)
    result = Dict{String, Any}(
        "success" => false,
        "termination_status" => "not_implemented",
        "solver_name" => "powermodels_security_constrained",
        "task" => "scopf",
    )

    try
        contingencies = haskey(payload, :contingencies) ? payload[:contingencies] : []
        isempty(contingencies) && throw(ArgumentError("at least one N-1 contingency is required"))
        pm_data = to_powermodels_data(case_data)
        add_scopf_contingencies!(pm_data, case_data, contingencies)
        multinetwork = build_c1_scopf_multinetwork(pm_data)

        options = haskey(payload, :options) ? payload[:options] : Dict()
        solver_attrs = Pair{String,Any}[
            "print_level" => 0,
            "sb" => "yes",
            "tol" => haskey(options, :tol) ? Float64(options[:tol]) : 1e-8,
        ]
        haskey(options, :max_iter) && push!(solver_attrs, "max_iter" => Int(options[:max_iter]))
        let linear_solver = String(strip(get(ENV, "IPOPT_LINEAR_SOLVER", "")))
            !isempty(linear_solver) && push!(solver_attrs, "linear_solver" => linear_solver)
        end
        let hsl_library = String(strip(get(ENV, "IPOPT_HSL_LIBRARY", "")))
            !isempty(hsl_library) && push!(solver_attrs, "hsllib" => hsl_library)
        end
        optimizer = optimizer_with_attributes(Ipopt.Optimizer, solver_attrs...)
        pm_out = run_c1_scopf(multinetwork, ACPPowerModel, optimizer)
        term = string(pm_out["termination_status"])
        result["success"] = term in ("LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL")
        result["termination_status"] = term
        result["objective"] = sanitize_json_value(get(pm_out, "objective", nothing))
        result["solve_time"] = sanitize_json_value(get(pm_out, "solve_time", nothing))
        result["contingency_count"] = length(contingencies)
        result["raw_result"] = sanitize_json_value(pm_out)
    catch err
        result["termination_status"] = err isa ArgumentError ? "invalid_input" : "exception"
        result["error"] = sprint(showerror, err)
        result["stacktrace"] = sprint(showerror, err, catch_backtrace())
    end
    return result
end

if length(ARGS) < 3
    error("usage: run_batch.jl <case_json> <payload_json> <out_json>")
end

case_data = JSON3.read(read(ARGS[1], String))
payload = JSON3.read(read(ARGS[2], String))
result = solve_scopf_request(case_data, payload)
open(ARGS[3], "w") do io
    JSON3.write(io, result)
end
