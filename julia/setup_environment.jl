using Pkg
Pkg.activate(@__DIR__)

# Frontier-safe defaults: avoid unstable precompile path on this environment.
ENV["JULIA_PKG_PRECOMPILE_AUTO"] = "0"
if !haskey(ENV, "JULIA_PKG_SERVER")
	ENV["JULIA_PKG_SERVER"] = ""
end

try
	Pkg.Registry.add("General")
catch
	# No-op if already present or inaccessible in the current environment.
end

Pkg.resolve()
Pkg.instantiate(; allow_autoprecomp=false)
println("Environment instantiated.")
