import os
import sys
from os import path

import ase.io
import numpy as np
import torch
from ase import Atoms
from ase.calculators.calculator import Calculator
from torch_geometric.data import Batch

from ocpmodels.common.efficient_validation.bfgs_torch import BFGS, TorchCalc
from ocpmodels.common.efficient_validation.lbfgs_torch import LBFGS
from ocpmodels.common.meter import mae, mae_ratio, mean_l2_distance
from ocpmodels.common.registry import registry
from ocpmodels.datasets.trajectory_lmdb import data_list_collater
from ocpmodels.preprocessing import AtomsToGraphs


class OCPCalculator(Calculator):
    implemented_properties = ["energy", "forces"]

    def __init__(self, trainer):
        """
        OCP-ASE Calculator

        Args:
            trainer: Object
                ML trainer for energy and force predictions.
        """
        Calculator.__init__(self)
        self.trainer = trainer
        self.a2g = AtomsToGraphs(
            max_neigh=200,
            radius=6,
            r_energy=False,
            r_forces=False,
            r_distances=False,
        )

    def train(self):
        self.trainer.train()

    def load_pretrained(self, checkpoint_path):
        """
        Load existing trained model

        Args:
            checkpoint_path: string
                Path to trained model
        """
        try:
            self.trainer.load_pretrained(checkpoint_path)
        except NotImplementedError:
            print("Unable to load checkpoint!")

    def calculate(self, atoms, properties, system_changes):
        Calculator.calculate(self, atoms, properties, system_changes)
        data_object = self.a2g.convert(atoms)
        batch = data_list_collater([data_object])
        predictions = self.trainer.predict(batch)

        self.results["energy"] = predictions["energy"][0]
        self.results["forces"] = predictions["forces"][0]


def relax_eval(batch, model, metric, steps, fmax, results_dir, relax_opt="bfgs"):
    """
    Evaluation of ML-based relaxations.
    Args:
        batch: object
        model: object
        metric: str
            Evaluation metric to be used.
        steps: int
            Max number of steps in the structure relaxation.
        fmax: float
                Structure relaxation terminates when the max force
                of the system is no bigger than fmax.
        results_dir: str
            Path to save model generated relaxations.
    """
    # TODO: batchify
    batch = batch[0]
    calc = TorchCalc(model)

    true_relaxed_pos = batch.pos_relaxed
    true_relaxed_energy = batch.y_relaxed

    # Run ML-based relaxation
    if relax_opt == "bfgs":
        dyn = BFGS(batch, calc)
    elif relax_opt == "lbfgs":
        dyn = LBFGS(batch, calc)
    else:
        raise ValueError(f"Unknown relax optimizer: {relax_opt}")

    ml_relaxed = dyn.run(fmax=fmax, steps=steps)

    ml_relaxed_energy = ml_relaxed.y[0].cpu()
    ml_relaxed_pos = ml_relaxed.pos.cpu()

    energy_error = eval(metric)(true_relaxed_energy, ml_relaxed_energy)
    structure_error = torch.mean(
        eval(metric)(ml_relaxed_pos, true_relaxed_pos,)
    )

    return energy_error, structure_error
