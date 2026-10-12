import os
import pandas as pd
import numpy as np
from param import time_steps, delta_t
from param import C_ev, eff_ev, C_bss, eff_bss, COP_hp, C_hp
import plotting as utils

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'realtime')


def compute_remaining_time(EV_connected):  # This function computes the remaining time for the current EV connection
    # Find the next time step where EV is disconnected and return its index
    remaining_time = next((i for i, value in enumerate(EV_connected) if value == 0), len(EV_connected)) 
    return remaining_time * delta_t

class Results:
    def __init__(self,Scenario):
        self.P_pv = np.zeros(time_steps)
        self.P_bss = np.zeros(time_steps)
        self.P_ev = np.zeros(time_steps)
        self.P_gen = np.zeros(time_steps)
        self.P_hp = np.zeros(time_steps)

        self.SOC_ev = np.zeros(time_steps)
        self.SOC_bss = np.zeros(time_steps)
        self.T_hp = np.zeros(time_steps)

        # Load data from CSV files into pandas DataFramess
        def _read(name):
            return pd.read_csv(os.path.join(DATA_DIR, name), delimiter=';', index_col=0)

        Load_data = _read('Load.csv')
        PV_data = _read('PV.csv')
        EV_data = _read('EV.csv')
        P_loss_data = _read('P_loss.csv')
        T_set_data = _read('T_set.csv')

        self.P_pv_max = np.array(PV_data[Scenario])[:time_steps]/100         # Scaled PV production in kW
        self.P_load = np.array(Load_data[Scenario])[:time_steps]/1000        # Scaled demand curve in kW
        self.EV_connected  = np.array(EV_data[Scenario])[:time_steps]        # EV connection status
        self.P_loss = np.array(P_loss_data[Scenario])[:time_steps]           # Power losses through envelopes in kW
        self.T_set = np.array(T_set_data[Scenario])[:time_steps]             # House temperature setpoint in °C


        # Initialize SOCs
        if   Scenario == 'S1': self.SOC_ev[0] = 0.6*C_ev ; self.SOC_bss[0] = 0.5*C_bss 
        elif Scenario == 'S2': self.SOC_ev[0] = 0.7*C_ev ; self.SOC_bss[0] = 0.5*C_bss
        elif Scenario == 'S3': self.SOC_ev[0] = 0.3*C_ev ; self.SOC_bss[0] = 0.5*C_bss 
        elif Scenario == 'S4': self.SOC_ev[0] = 0.3*C_ev ; self.SOC_bss[0] = 0.5*C_bss          

        self.t = np.linspace(0, time_steps*delta_t, time_steps)  

    def append_res(self, P_pv, P_gen, P_bss, P_ev, P_hp, t):
        self.P_pv[t] = P_pv
        self.P_bss[t] = P_bss
        self.P_ev[t] = P_ev
        self.P_gen[t] = P_gen
        self.P_hp[t] = P_hp
        self.state_update(P_ev,P_bss,P_hp,t)

    def state_update(self, P_ev, P_bss, P_hp, t):
        if t == 0: return KeyError
        self.SOC_ev[t] = self.SOC_ev[t-1] + delta_t * max(P_ev,0)*eff_ev  + delta_t * min(P_ev,0) / eff_ev
        self.SOC_bss[t] = self.SOC_bss[t-1] + delta_t * max(P_bss,0)*eff_bss + delta_t * min(P_bss,0) / eff_bss
        self.T_hp[t] = self.T_hp[t-1] + delta_t * (P_hp*COP_hp - self.P_loss[t])/C_hp

def run_sim(control_function, Scenario, model=None, verbose=True):      
    # Initialize arrays to store time-series results for various parameters
    results = Results(Scenario)

    for t in range(1, time_steps):
        # Compute the remaining time for EV connection at each time step
        EV_remaining_time = compute_remaining_time(results.EV_connected[t:]) if results.EV_connected[t] else 0

        P_l = results.P_load[t]
        P_pv = results.P_pv_max[t]
        EV_c = results.EV_connected[t]
        P_loss = results.P_loss[t]
        T_set = results.T_set[t]

        # Call the control function to determine the power outputs
        P_pv, P_gen, P_bss, P_ev, P_hp = control_function(P_l, P_pv, EV_c,
                                                    EV_remaining_time, results.P_gen[t-1], 
                                                    results.SOC_bss[t-1]/C_bss, results.SOC_ev[t-1]/C_ev,
                                                    P_loss, T_set, results.T_hp[t-1], model)
        
        # Append results to the results object and update SOCs
        results.append_res(P_pv, P_gen, P_bss, P_ev, P_hp, t)

    if verbose:
        print("Simulation completed")
        utils.check_res(results)
        utils.print_res(results)
    #utils.plot_res(results)
    return results
