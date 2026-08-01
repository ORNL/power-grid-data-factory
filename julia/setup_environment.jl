using Pkg
Pkg.activate(@__DIR__)

try
	Pkg.Registry.add("General")
catch
	# No-op if already present or inaccessible in the current environment.
end

Pkg.resolve()
Pkg.instantiate()
println("Environment instantiated.")
