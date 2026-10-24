import unittest

import torch

from ml.losses.physics_loss import physics_constrained_loss
from ml.models.multiscale_residual_cnn import MultiScaleResidualCNN
from ml.models.gated_era5_fusion import GatedERA5Fusion


class MultiScaleModelTests(unittest.TestCase):
    def test_point_and_quantile_heads_accept_long_motion_windows(self):
        values = torch.randn(3, 12, 8)
        point = MultiScaleResidualCNN(quantile=False).eval()(values)
        quantile = MultiScaleResidualCNN(quantile=True).eval()(values)
        self.assertEqual(tuple(point["track"].shape), (3, 6, 2))
        self.assertIsNone(point["quantiles"])
        self.assertEqual(tuple(quantile["track"].shape), (3, 6, 2))
        self.assertEqual(tuple(quantile["quantiles"].shape), (3, 6, 3, 2))
        self.assertEqual(tuple(quantile["wind"].shape), (3, 6, 1))

    def test_physics_loss_is_finite_and_differentiable(self):
        batch = 2
        target = torch.randn(batch, 6, 3)
        track = torch.randn(batch, 6, 2, requires_grad=True)
        wind = torch.randn(batch, 6, 1, requires_grad=True)
        loss = physics_constrained_loss(
            {"track": track, "wind": wind, "quantiles": None},
            target,
            torch.zeros(3),
            torch.ones(3),
        )
        self.assertTrue(torch.isfinite(loss["loss"]))
        loss["loss"].backward()
        self.assertIsNotNone(track.grad)
        self.assertIsNotNone(wind.grad)

    def test_quantile_loss_backpropagates(self):
        predictions = torch.randn(2, 6, 3, 2, requires_grad=True)
        target = torch.randn(2, 6, 2)
        loss = physics_constrained_loss(
            {"track": predictions[:, :, 1], "wind": torch.zeros(2, 6, 1), "quantiles": predictions},
            target=torch.cat((target, torch.zeros(2, 6, 1)), dim=-1),
            target_mean=torch.zeros(3),
            target_std=torch.ones(3),
        )["loss"]
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertIsNotNone(predictions.grad)

    def test_era5_fusion_falls_back_to_track_features_when_unavailable(self):
        model = GatedERA5Fusion()
        track = torch.randn(2, 12, 8)
        era5 = torch.randn(2, 12, 4)
        expected = model(track, era5, torch.zeros(2, 12))
        fallback = model(track, era5)
        torch.testing.assert_close(expected, fallback)
        masked = model(track, era5, torch.ones(2, 12))
        self.assertFalse(torch.allclose(fallback, masked))


if __name__ == "__main__":
    unittest.main()
