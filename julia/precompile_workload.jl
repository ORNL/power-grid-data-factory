# Representative workload traced by PackageCompiler when building the sysimage.
# Running a real AC-OPF solve forces PowerModels/Ipopt/JSON3 method compilation
# to be captured in the image so campaign solves skip first-run compilation.
using JSON3

const _HERE = @__DIR__

# Tiny 3-bus case matching the schema run_opf.jl expects.
case = Dict(
    "case_id" => "precompile_case",
    "base_mva" => 100.0,
    "buses" => [
        Dict("bus_id" => "1", "type" => 3, "vm" => 1.0, "va" => 0.0, "vmin" => 0.9, "vmax" => 1.1),
        Dict("bus_id" => "2", "type" => 2, "vm" => 1.0, "va" => 0.0, "vmin" => 0.9, "vmax" => 1.1),
        Dict("bus_id" => "3", "type" => 1, "vm" => 1.0, "va" => 0.0, "vmin" => 0.9, "vmax" => 1.1),
    ],
    "generators" => [
        Dict("bus_id" => "1", "gen_id" => "1", "pmin" => 0.0, "pmax" => 300.0, "qmin" => -300.0, "qmax" => 300.0, "cost" => [0.01, 10.0, 0.0]),
        Dict("bus_id" => "2", "gen_id" => "2", "pmin" => 0.0, "pmax" => 300.0, "qmin" => -300.0, "qmax" => 300.0, "cost" => [0.01, 10.0, 0.0]),
    ],
    "branches" => [
        Dict("from" => "1", "to" => "2", "r" => 0.01, "x" => 0.10, "rate_a" => 250.0),
        Dict("from" => "2", "to" => "3", "r" => 0.01, "x" => 0.10, "rate_a" => 250.0),
        Dict("from" => "1", "to" => "3", "r" => 0.01, "x" => 0.10, "rate_a" => 250.0),
    ],
    "loads" => [
        Dict("bus_id" => "3", "load_id" => "1", "pd" => 250.0, "qd" => 50.0),
    ],
)
payload = Dict("task" => "ac_opf", "options" => Dict())

case_path = tempname() * ".json"
payload_path = tempname() * ".json"
out_path = tempname() * ".json"
open(io -> JSON3.write(io, case), case_path, "w")
open(io -> JSON3.write(io, payload), payload_path, "w")

# Drive the exact production solve path (to_powermodels_data + solve_opf).
empty!(ARGS)
append!(ARGS, [case_path, payload_path, out_path])
include(joinpath(_HERE, "run_opf.jl"))

try
    res = JSON3.read(read(out_path, String))
    println("precompile_workload: success=", get(res, :success, false),
            " termination=", get(res, :termination_status, "?"))
catch err
    println("precompile_workload: workload finished with warning: ", err)
end

for p in (case_path, payload_path, out_path)
    try
        rm(p; force=true)
    catch
    end
end
