\
\
\
\

from __future__ import annotations

import numpy as np
import torch
from torch import nn

DEFAULT_PERCENTILE = 99.5

class GRUAutoencoder(nn.Module):
    def __init__(self, n_features: int, hidden: int = 64, layers: int = 1):
        super().__init__()
        self.encoder = nn.GRU(n_features, hidden, layers, batch_first=True)
        self.decoder = nn.GRU(hidden, hidden, layers, batch_first=True)
        self.output = nn.Linear(hidden, n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, state = self.encoder(x)
        context = state[-1].unsqueeze(1).repeat(1, x.size(1), 1)
        decoded, _ = self.decoder(context)
        return self.output(decoded)

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())

def reconstruction_errors(model: nn.Module, X: np.ndarray,
                          batch: int = 256) -> np.ndarray:

    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), batch):
            x = torch.from_numpy(X[i:i + batch])
            out.append(((model(x) - x) ** 2).mean(dim=(1, 2)).numpy())
    return np.concatenate(out) if out else np.empty(0, dtype="float32")

def alarm_threshold(errors: np.ndarray,
                    percentile: float = DEFAULT_PERCENTILE) -> float:
    return float(np.percentile(errors, percentile)) if len(errors) else float("inf")

def train(model: nn.Module, X: np.ndarray, epochs: int = 20, batch: int = 64,
          lr: float = 1e-3, mu: float = 0.0,
          global_weights: list[np.ndarray] | None = None) -> float:
\

    if len(X) == 0:
        return float("nan")
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()
    anchor = ([torch.from_numpy(w) for w in global_weights]
              if mu > 0 and global_weights is not None else None)

    loss_value = float("nan")
    for _ in range(epochs):
        order = np.random.permutation(len(X))
        total, seen = 0.0, 0
        for i in range(0, len(X), batch):
            x = torch.from_numpy(X[order[i:i + batch]])
            optimizer.zero_grad()
            loss = criterion(model(x), x)
            if anchor is not None:
                penalty = sum(((p - g) ** 2).sum()
                              for p, g in zip(model.parameters(), anchor))
                loss = loss + (mu / 2) * penalty
            loss.backward()
            optimizer.step()
            total += loss.item() * len(x)
            seen += len(x)
        loss_value = total / max(seen, 1)
    return loss_value

def weights_to_arrays(model: nn.Module) -> list[np.ndarray]:
    return [p.detach().cpu().numpy() for p in model.state_dict().values()]

def arrays_to_weights(model: nn.Module, arrays: list[np.ndarray]) -> None:
    state = model.state_dict()
    for key, value in zip(state.keys(), arrays):
        state[key] = torch.from_numpy(np.asarray(value))
    model.load_state_dict(state, strict=True)
