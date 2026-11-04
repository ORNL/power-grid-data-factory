using Dates
using JSON3
using LinearAlgebra
using SHA
using SparseArrays
using Statistics
using PowerModels

include("run_opf.jl")
include("run_pf.jl")
include("pf_sensitivities.jl")

const SOURCE_PATH = "data/outputs/runs_3b/mapreduce_round_002/shard_05168/ac_opf/samples.jsonl"
const CASE_PATH = "external/pglib-opf/pglib_opf_case14_ieee.m"
const OUTPUT_DIR = "data/derived/pf_sensitivities_pilot_v4"
const MAX_ACCEPTED = 8
const MAX_SCANNED = 500
const RESIDUAL_TOLERANCE = 1e-8
const DERIVATIVE_TOLERANCE = 1e-8

function json_value(value)
    if value isa AbstractDict
        return Dict{String, Any}(string(key) => json_value(item) for (key, item) in value)
    elseif value isa AbstractVector
        return [json_value(item) for item in value]
    elseif value isa Symbol
        return string(value)
    elseif value isa AbstractFloat
        return isfinite(value) ? value : nothing
    end
    return value
end

function file_sha256(path)
    return bytes2hex(open(sha256, path))
end

function string_keyed_dict(value)
    return Dict(string(key) => item for (key, item) in pairs(value))
end

function mutable_symbol_data(value)
    if value isa AbstractDict
        return Dict{Symbol, Any}(Symbol(key) => mutable_symbol_data(item) for (key, item) in pairs(value))
    elseif value isa AbstractVector
        return [mutable_symbol_data(item) for item in value]
    end
    return value
end

function bus_solution(solution, bus_id)
    return solution["bus"][string(bus_id)]
end

function prepare_record(record, intact_case)
    get(record, :success, false) === true || throw(ArgumentError("source solver result is not successful"))
    resolved = mutable_symbol_data(record[:inputs][:resolved_case])
    candidate = record[:inputs][:candidate]
    solution = record[:result][:raw_result][:solution]
    String(record[:task]) == "ac_opf" || throw(ArgumentError("source task is not AC-OPF"))
    expected_case_id = splitext(basename(CASE_PATH))[1]
    String(record[:case_id]) == expected_case_id || throw(ArgumentError("case ID does not match intact source case"))

    source_branches = Set("branch_$(lpad(string(branch["index"]), 6, '0'))" for branch in values(intact_case["branch"]))
    resolved_branches = Set(String(branch[:branch_id]) for branch in resolved[:branches])
    declared_outages = Set{String}(String.(candidate[:switched_off_branches]))
    for component in candidate[:contingency][:components]
        String(component[:type]) == "branch" || throw(ArgumentError("unsupported non-branch contingency"))
        push!(declared_outages, String(component[:id]))
    end
    all(id -> id in source_branches, declared_outages) || throw(ArgumentError("declared outage references unknown source branch"))
    expected_branches = setdiff(source_branches, declared_outages)
    resolved_branches == expected_branches || throw(ArgumentError("resolved topology does not match declared branch outages"))

    resolved_bus_ids = Set(String(bus[:bus_id]) for bus in resolved[:buses])
    intact_bus_ids = Set(string(bus["bus_i"]) for bus in values(intact_case["bus"]))
    resolved_bus_ids == intact_bus_ids || throw(ArgumentError("resolved bus IDs differ from intact case"))
    solution_bus_ids = Set(string(key) for key in keys(solution["bus"]))
    solution_bus_ids == resolved_bus_ids || throw(ArgumentError("saved solution bus IDs differ from resolved case"))
    resolved_case_sha256 = bytes2hex(sha256(JSON3.write(record[:inputs][:resolved_case])))

    for bus in resolved[:buses]
        id = String(bus[:bus_id])
        saved_bus = bus_solution(solution, id)
        bus[:vm] = Float64(saved_bus[:vm])
        bus[:va] = Float64(saved_bus[:va])
    end

    solution_gens = string_keyed_dict(solution["gen"])
    for (index, generator) in enumerate(resolved[:generators])
        saved = get(solution_gens, string(index), nothing)
        saved === nothing && throw(ArgumentError("saved solution omits generator index $(index)"))
        generator[:pg] = Float64(saved[:pg]) * Float64(solution[:baseMVA])
        generator[:qg] = Float64(saved[:qg]) * Float64(solution[:baseMVA])
        generator[:vg] = Float64(bus_solution(solution, String(generator[:bus_id]))[:vm])
    end

    pm_data = to_powermodels_data(resolved)
    normalize_pf_generator_buses!(pm_data)
    return pm_data, resolved, candidate, solution, resolved_case_sha256
end

function finite_difference_check(pm_data, model; step=1e-5)
    errors = Float64[]
    for (column, kind) in enumerate(model.control_kinds)
        plus = deepcopy(pm_data)
        minus = deepcopy(pm_data)
        if kind == "pg"
            generator_id = string(model.control_ids[column])
            plus["gen"][generator_id]["pg"] += step / pm_data["baseMVA"]
            minus["gen"][generator_id]["pg"] -= step / pm_data["baseMVA"]
        else
            bus_id = string(model.control_ids[column])
            generator = only([gen for gen in values(plus["gen"]) if Int(gen["gen_bus"]) == Int(model.control_ids[column]) && gen["gen_status"] != 0])
            generator_id = string(generator["index"])
            plus["gen"][generator_id]["vg"] += step
            minus["gen"][generator_id]["vg"] -= step
        end
        plus_model = PFSensitivities.linearize(plus; state=model.state, control_names=[model.controls[column]])
        minus_model = PFSensitivities.linearize(minus; state=model.state, control_names=[model.controls[column]])
        numeric = (plus_model.residual - minus_model.residual) / (2step)
        analytic = Array(model.input_jacobian[:, column])
        push!(errors, maximum(abs, numeric - analytic))
    end
    return maximum(errors; init=0.0)
end

function make_label(record, resolved, candidate, model, raw_line, resolved_case_sha256, finite_difference_error, elapsed)
    state_count = length(model.bus_ids) - 1
    angle_sensitivity = Dict(
        "bus_ids" => model.state_outputs.angle_bus_ids,
        "control_names" => model.controls,
        "shape" => [length(model.state_outputs.angle_bus_ids), length(model.controls)],
        "units" => "radian per control unit",
        "values" => model.sensitivity[1:state_count, :],
    )
    voltage_sensitivity = Dict(
        "bus_ids" => model.state_outputs.voltage_bus_ids,
        "control_names" => model.controls,
        "shape" => [length(model.state_outputs.voltage_bus_ids), length(model.controls)],
        "units" => "p.u. per control unit",
        "values" => model.sensitivity[(state_count + 1):end, :],
    )
    q_headroom = minimum(min(Float64(gen[:qmax]) - Float64(gen[:qg]), Float64(gen[:qg]) - Float64(gen[:qmin])) for gen in resolved[:generators] if Int(get(gen, :status, 1)) > 0)
    return Dict(
        "schema_version" => "pf_sensitivity_label_v1",
        "source" => Dict(
            "candidate_id" => String(record[:candidate_id]),
            "run_id" => String(record[:run_id]),
            "source_record_sha256" => bytes2hex(sha256(raw_line)),
            "resolved_case_sha256" => resolved_case_sha256,
            "resolved_case_hash_basis" => "source inputs.resolved_case before saved solution values are applied",
            "source_file" => SOURCE_PATH,
            "source_file_sha256" => file_sha256(SOURCE_PATH),
        ),
        "scenario" => Dict(
            "case_id" => String(record[:case_id]),
            "topology_id" => String(record[:topology_id]),
            "contingency_set_id" => String(record[:contingency_set_id]),
            "switched_off_branches" => String.(candidate[:switched_off_branches]),
            "contingency_components" => json_value(candidate[:contingency][:components]),
        ),
        "operating_point" => Dict(
            "mode" => "fixed_pv_pq_from_saved_ac_opf",
            "pf_residual_linf_pu" => maximum(abs, model.residual),
            "pf_residual_l2_pu" => norm(model.residual),
            "minimum_generator_q_headroom_mvar" => q_headroom,
            "near_q_limit_threshold_mvar" => 2.0,
            "q_limit_proximity" => q_headroom <= 2.0 ? "near_q_limit_boundary" : "not_near_q_limit",
        ),
        "controls" => Dict(
            "names" => model.controls,
            "bus_ids" => model.control_bus_ids,
            "kinds" => model.control_kinds,
            "units" => [kind == "pg" ? "MW" : "p.u." for kind in model.control_kinds],
            "slack_pg_included" => false,
        ),
        "outputs" => Dict("bus_angle" => angle_sensitivity, "bus_voltage_magnitude" => voltage_sensitivity),
        "derivative_convention" => Dict(
            "equation" => "A * dz_du + B = 0",
            "solution" => "dz_du = -(A \\ B)",
            "bus_angle_reference" => model.reference_bus_id,
            "bus_angle_reference_sensitivity" => 0.0,
            "state_order" => vcat(model.state_outputs.angle_bus_ids, model.state_outputs.voltage_bus_ids),
            "direct_output_term" => "zero for bus voltage magnitude and angle as state outputs",
            "branch_output_derivatives" => "not included in this pilot",
        ),
        "quality" => Dict(
            "finite_values" => all(isfinite, model.sensitivity),
            "jacobian_nnz" => nnz(model.jacobian),
            "state_count" => length(model.state),
            "control_count" => length(model.controls),
            "linear_solve_residual_linf" => maximum(abs, model.jacobian * model.sensitivity + model.input_jacobian),
            "input_jacobian_finite_difference_linf" => finite_difference_error,
            "record_processing_seconds" => elapsed,
        ),
    )
end

function main()
    isfile(SOURCE_PATH) || error("Missing source JSONL: $(SOURCE_PATH)")
    isfile(CASE_PATH) || error("Missing intact MATPOWER case: $(CASE_PATH)")
    !ispath(OUTPUT_DIR) || error("Refusing to overwrite existing pilot output: $(OUTPUT_DIR)")
    intact_case = PowerModels.parse_file(CASE_PATH)
    labels = Dict{String, Any}[]
    exclusions = Dict{String, Int}()
    scanned = 0
    source_bytes = stat(SOURCE_PATH).size
    started = time()
    open(SOURCE_PATH, "r") do io
        for line in eachline(io)
            scanned >= MAX_SCANNED && break
            length(labels) >= MAX_ACCEPTED && break
            scanned += 1
            record = JSON3.read(line)
            try
                record_started = time()
                pm_data, resolved, candidate, solution, resolved_case_sha256 = prepare_record(record, intact_case)
                model = PFSensitivities.linearize(pm_data)
                residual_linf = maximum(abs, model.residual)
                residual_linf <= RESIDUAL_TOLERANCE || throw(ArgumentError("fixed-mode PF residual exceeds tolerance"))
                all(isfinite, model.sensitivity) || throw(ArgumentError("sensitivity contains non-finite values"))
                derivative_error = finite_difference_check(pm_data, model)
                derivative_error <= DERIVATIVE_TOLERANCE || throw(ArgumentError("input Jacobian finite-difference error exceeds tolerance"))
                label = make_label(record, resolved, candidate, model, line, resolved_case_sha256, derivative_error, 0.0)
                label["quality"]["record_processing_seconds"] = time() - record_started
                push!(labels, label)
            catch error
                reason = replace(sprint(showerror, error), r"\s+" => " ")
                exclusions[reason] = get(exclusions, reason, 0) + 1
            end
        end
    end
    isempty(labels) && error("No records passed the fixed-mode PF pilot screens; exclusions=$(exclusions)")
    mkpath(OUTPUT_DIR)
    label_path = joinpath(OUTPUT_DIR, "labels.jsonl")
    open(label_path, "w") do io
        for label in labels
            JSON3.write(io, json_value(label))
            write(io, '\n')
        end
    end
    elapsed = time() - started
    report = Dict(
        "schema_version" => "pf_sensitivity_pilot_manifest_v1",
        "created_utc" => string(Dates.now(Dates.UTC)),
        "scope" => "bounded local post-processing of existing successful AC-OPF records; no PF solve or record mutation",
        "source_file" => SOURCE_PATH,
        "source_file_sha256" => file_sha256(SOURCE_PATH),
        "source_bytes" => source_bytes,
        "intact_case_file" => CASE_PATH,
        "intact_case_sha256" => file_sha256(CASE_PATH),
        "linearizer_sha256" => file_sha256("julia/pf_sensitivities.jl"),
        "driver_sha256" => file_sha256(@__FILE__),
        "accepted_count" => length(labels),
        "scanned_count" => scanned,
        "max_accepted" => MAX_ACCEPTED,
        "max_scanned" => MAX_SCANNED,
        "residual_tolerance_pu" => RESIDUAL_TOLERANCE,
        "derivative_tolerance" => DERIVATIVE_TOLERANCE,
        "exclusion_counts" => exclusions,
        "elapsed_seconds" => elapsed,
        "output_bytes" => stat(label_path).size,
        "output_sha256" => file_sha256(label_path),
        "julia_version" => string(VERSION),
        "powermodels_version" => string(Base.pkgversion(PowerModels)),
        "linear_solver" => "Julia SparseArrays LU (UMFPACK)",
        "limitations" => [
            "fixed PV/PQ mode only; no PV-to-PQ or active-limit mode transitions",
            "bus aggregate reactive response omitted because individual generator Q allocation is ambiguous",
            "dense state-by-control sensitivity outputs are pilot-scale only",
            "saved AC-OPF primals are differentiated as PF controls, not as optimizer solutions",
        ],
    )
    open(joinpath(OUTPUT_DIR, "manifest.json"), "w") do io
        JSON3.write(io, json_value(report); indent=2)
        write(io, '\n')
    end
    println(JSON3.write(json_value(report); indent=2))
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end