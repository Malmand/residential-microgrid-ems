# Simulation Parameters
#total_hours = 24                # Total simulation duration in hours
time_steps = 961                # Total number of discrete time steps in the simulation
delta_t = 1 / 40                # Duration of each time step in hours (1.5 minutes)

# PV Parameters
P_nom_pv = 10                   # Peak power capacity of the PV system in kW

# Genset Parameters
P_max_gen = 10                  # Maximum power output for the generator in kW
P_min_gen = 2                   # Minimum power output required to run the generator in kW

# Battery Parameters
C_bss = 40                      # Total storage capacity of the battery in kWh
SOC_min_bss = 0.2               # Minimum SOC for battery as a fraction of Battery_Capacity [0, 1]
SOC_max_bss = 0.85              # Maximum SOC for battery as a fraction of Battery_Capacity [0, 1]
eff_bss = 0.95                  # Efficiency for battery charging process [0, 1]
P_nom_bss = 10                  # Maximum power output (charge or discharge) for the battery in kW

# EV Parameters
SOC_target_ev = 0.8             # Target SOC for the EV
C_ev = 60                       # Total storage capacity of the EV battery in kWh
eff_ev = 0.98                   # Efficiency for EV dis.charging process [0, 1]
SOC_min_ev = 0.2                # Minimum SOC for the EV battery [0, 1]
SOC_max_ev = 0.95               # Maximum SOC for the EV battery [0, 1]
P_nom_ev = 10                   # Maximum power output (charge or discharge) for the EV in kW


# HP parameters
P_max_hp = 5              
COP_hp = 2.5
C_hp = (307781.25 + 0.5*307781.25)/(1e3*60*60)
delta_T_max = 20


#             "C_env": 25270000.0,
#             "C_air": 307781.25,
# C = (user["HP_data"]["C_air"]+0.5*user["HP_data"]["C_env"]))/(1e3*60*60)