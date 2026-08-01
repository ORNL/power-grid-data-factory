using JSON3

if length(ARGS) < 3
    error("usage: run_batch.jl <case_json> <payload_json> <out_json>")
end

out_path = ARGS[3]
result = Dict(
    "success" => false,
    "termination_status" => "not_implemented",
    "solver_name" => "powermodels",
    "task" => "batch",
)

open(out_path, "w") do io
    JSON3.write(io, result)
end
