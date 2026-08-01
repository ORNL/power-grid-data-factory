using JSON3

if length(ARGS) < 3
    error("usage: run_pf.jl <case_json> <payload_json> <out_json>")
end

case_path = ARGS[1]
payload_path = ARGS[2]
out_path = ARGS[3]

case_data = JSON3.read(read(case_path, String))
payload = JSON3.read(read(payload_path, String))

result = Dict(
    "success" => false,
    "termination_status" => "not_implemented",
    "solver_name" => "powermodels",
    "task" => "pf",
    "note" => "Replace with full PowerModels PF implementation",
)

open(out_path, "w") do io
    JSON3.write(io, result)
end
