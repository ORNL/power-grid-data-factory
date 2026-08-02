using JSON3
using Ipopt
using PowerModels

if length(ARGS) < 3
    error("usage: run_opf.jl <case_json> <payload_json> <out_json>")
end

case_path = ARGS[1]
payload_path = ARGS[2]
out_path = ARGS[3]

case_data = JSON3.read(read(case_path, String))
payload = JSON3.read(read(payload_path, String))

task = haskey(payload, :task) ? String(payload[:task]) : "ac_opf"

function sanitize_json_value(x)
    if x isa AbstractDict
        out = Dict{String, Any}()
        for (k, v) in x
            out[string(k)] = sanitize_json_value(v)
        end
        return out
    elseif x isa AbstractVector
        return [sanitize_json_value(v) for v in x]
    elseif x isa AbstractFloat
        return isfinite(x) ? x : nothing
    else
        return x
    end
end

function to_powermodels_data(case_data)
    base_mva = haskey(case_data, :base_mva) ? Float64(case_data[:base_mva]) : 100.0
    buses = case_data[:buses]
    gens = case_data[:generators]
    branches = case_data[:branches]
    loads = haskey(case_data, :loads) ? case_data[:loads] : []

    pd_by_bus = Dict{Int, Float64}()
    qd_by_bus = Dict{Int, Float64}()
    for load in loads
        bid = parse(Int, String(load[:bus_id]))
        pd_by_bus[bid] = get(pd_by_bus, bid, 0.0) + Float64(load[:pd])
        qd_by_bus[bid] = get(qd_by_bus, bid, 0.0) + Float64(load[:qd])
    end

    pm_bus = Dict{String, Any}()
    for (idx, b) in enumerate(buses)
        bid = parse(Int, String(b[:bus_id]))
        btype = Int(b[:type])
        vm = Float64(b[:vm])
        va = Float64(b[:va])
        vmin = Float64(b[:vmin])
        vmax = Float64(b[:vmax])
        pm_bus[string(idx)] = Dict(
            "index" => idx,
            "bus_i" => bid,
            "bus_type" => btype,
            # Keep bus-level demand at zero and model demands explicitly in `load`.
            "pd" => 0.0,
            "qd" => 0.0,
            "gs" => 0.0,
            "bs" => 0.0,
            "vm" => vm,
            "va" => va,
            "base_kv" => 230.0,
            "zone" => 1,
            "vmax" => vmax,
            "vmin" => vmin,
        )
    end

    bus_index_by_id = Dict{Int, Int}()
    for (k, b) in pm_bus
        bus_index_by_id[Int(b["bus_i"])] = Int(b["index"])
    end

    pm_gen = Dict{String, Any}()
    for (idx, g) in enumerate(gens)
        bus_id = parse(Int, String(g[:bus_id]))
        bus_idx = bus_index_by_id[bus_id]
        pmin = Float64(g[:pmin]) / base_mva
        pmax = Float64(g[:pmax]) / base_mva
        qmin = Float64(g[:qmin]) / base_mva
        qmax = Float64(g[:qmax]) / base_mva
        c = haskey(g, :cost) ? g[:cost] : [0.0, 1.0, 0.0]
        # Interpret provided coefficients as [quad, linear, const].
        c2 = length(c) >= 1 ? Float64(c[1]) : 0.0
        c1 = length(c) >= 2 ? Float64(c[2]) : 1.0
        c0 = length(c) >= 3 ? Float64(c[3]) : 0.0

        pm_gen[string(idx)] = Dict(
            "index" => idx,
            "gen_bus" => bus_id,
            "gen_status" => 1,
            "pg" => max(min((pmin + pmax) / 2, pmax), pmin),
            "qg" => 0.0,
            "qmax" => qmax,
            "qmin" => qmin,
            "vg" => 1.0,
            "mbase" => 1.0,
            "pmax" => pmax,
            "pmin" => pmin,
            "source_id" => Any["gen", bus_id, String(g[:gen_id])],
            "bus_idx" => bus_idx,
            "model" => 2,
            "ncost" => 3,
            "cost" => [c2, c1, c0],
            "startup" => 0.0,
            "shutdown" => 0.0,
        )
    end

    pm_branch = Dict{String, Any}()
    for (idx, br) in enumerate(branches)
        f_bus = parse(Int, String(br[:from]))
        t_bus = parse(Int, String(br[:to]))
        pm_branch[string(idx)] = Dict(
            "index" => idx,
            "f_bus" => f_bus,
            "t_bus" => t_bus,
            "br_r" => Float64(br[:r]),
            "br_x" => Float64(br[:x]),
            "b_fr" => 0.0,
            "b_to" => 0.0,
            "g_fr" => 0.0,
            "g_to" => 0.0,
            "br_status" => 1,
            "rate_a" => Float64(br[:rate_a]) / base_mva,
            "rate_b" => Float64(br[:rate_a]) / base_mva,
            "rate_c" => Float64(br[:rate_a]) / base_mva,
            "angmin" => -60.0,
            "angmax" => 60.0,
            "tap" => 1.0,
            "shift" => 0.0,
            "transformer" => false,
        )
    end

    pm_load = Dict{String, Any}()
    for (idx, ld) in enumerate(loads)
        bus_id = parse(Int, String(ld[:bus_id]))
        load_id = haskey(ld, :load_id) ? String(ld[:load_id]) : string(idx)
        pm_load[string(idx)] = Dict(
            "index" => idx,
            "status" => 1,
            "load_bus" => bus_id,
            "pd" => Float64(ld[:pd]) / base_mva,
            "qd" => Float64(ld[:qd]) / base_mva,
            "source_id" => Any["load", bus_id, load_id],
        )
    end

    return Dict(
        "name" => haskey(case_data, :case_id) ? String(case_data[:case_id]) : "case",
        "baseMVA" => base_mva,
        "per_unit" => true,
        "source_type" => "json",
        "bus" => pm_bus,
        "gen" => pm_gen,
        "load" => pm_load,
        "shunt" => Dict{String, Any}(),
        "branch" => pm_branch,
        "dcline" => Dict{String, Any}(),
        "storage" => Dict{String, Any}(),
        "switch" => Dict{String, Any}(),
    )
end

result = Dict{String, Any}(
    "success" => false,
    "termination_status" => "not_implemented",
    "solver_name" => "powermodels",
    "task" => task,
)

if task == "ac_opf"
    try
        pm_data = to_powermodels_data(case_data)
        optimizer = optimizer_with_attributes(
            Ipopt.Optimizer,
            "print_level" => 0,
            "sb" => "yes",
            "tol" => 1e-8,
        )
        pm_out = solve_opf(pm_data, ACPPowerModel, optimizer)
        term = string(pm_out["termination_status"])
        ok = term in ("LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL")
        result["success"] = ok
        result["termination_status"] = term
        result["objective"] = sanitize_json_value(get(pm_out, "objective", nothing))
        result["solve_time"] = sanitize_json_value(get(pm_out, "solve_time", nothing))
        result["raw_result"] = sanitize_json_value(pm_out)
    catch err
        result["success"] = false
        result["termination_status"] = "exception"
        result["error"] = sprint(showerror, err)
        result["stacktrace"] = sprint(showerror, err, catch_backtrace())
    end
else
    result["success"] = false
    result["termination_status"] = "not_implemented"
    result["note"] = "Only ac_opf task is currently implemented in run_opf.jl"
end

open(out_path, "w") do io
    JSON3.write(io, result)
end
