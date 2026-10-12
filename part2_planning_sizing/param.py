import pandas as pd
from scipy.interpolate import interp1d
import numpy as np
import matplotlib.pyplot as plt

# Simulation Parameters
delta_t = 1 / 4                                    # Duration of each time step [hour]
inv_hor = 20                                       # Investment horizon [year]

# =============================================================================
#  Costs
# ==============  Operation  ==================================================
PI_gen = 0.6                          # Fuel costs for generator [EUR/kWh]
PI_imp = 0.4                          # Cost of importing energy [EUR/kWh]
PI_exp = 0.03                         # Revenue from exporting energy [EUR/kWh]
# ==============  Investment  =================================================
PI_c_pv = 1800                        # Cost of PV capacity [EUR/kWp]
PI_c_bss = 280                        # Cost of battery capacity [EUR/kWh]
PI_c_inv = 150                        # Cost of inverter capacity [EUR/kW]
PI_c_gen = 150                        # Cost of generator capacity [EUR/kW]
# =============================================================================
C_pv_max = 50                      # Maximum PV system size [kWp]
C_bss_max = 100                    # Maximum Battery capacity [kWh]

# Battery Parameters
SOC_0_bss = 0.5                 # Initial SOC for battery as a fraction of C_bss [0, 1]
SOC_min_bss = 0.2               # Minimum SOC for battery as a fraction of C_bss [0, 1]
SOC_max_bss = 0.85              # Maximum SOC for battery as a fraction of C_bss [0, 1]
eff_bss = 0.95                  # Efficiency for battery charging process [0, 1]

# EV Parameters
SOC_target_ev = 0.8             # Target SOC for the EV [0, 1]
C_ev = 60                       # Total storage capacity of the EV battery [kWh]
P_nom_ev = 10                   # Nominal power of the EV charger [kW]
eff_ev = 0.98                   # Efficiency for EV dis.charging process [0, 1]
SOC_min_ev = 0.2                # Minimum SOC for the EV battery [0, 1]
SOC_max_ev = 0.95               # Maximum SOC for the EV battery [0, 1]

# HP Parameters 
P_max_hp = 10 #Nominal power of the HP [kW]
COP_hp = 2.5 # Coefficient of Performance [kWth/kWe]
delta_T_max = 0.5 # Accepted temperature range [ï¿½]
C_hp = (307781.25 + 0.5*307781.25)/(1e3*60*60) # Home thermal inertia [kWh/ï¿½]



## CO2
# Variable emission
FUEL_CO2 = 0.300 #g/Wh
# You can add a CO2 emission for grid import in non islanded mode

# Fixed emission
PV_CO2 = 1.8e3 # g/Wp
STORAGE_CO2 = 150 #g/Wh
GENSET_CO2 = 10 #g/W
INVERTER_CO2 = 60 #g/VA
T_up = 4