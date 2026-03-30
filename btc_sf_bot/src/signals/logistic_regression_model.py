"""
Logistic Regression Model for SmartFlow Signal Prediction
Learns from historical trades to predict win probability

Features (Flow-First Hybrid):
Primary Flow Features (The Engine):
- oi_shock: Normalized OI change
- cvd_aggression: CVD Delta relative to volatility
- volume_surge_ratio: Current volume vs 50-period MA
- wall_imbalance: Bid vs Ask wall strength ratio
- funding_bias: Funding rate (contrarian indicator)
- delta_divergence_signal: Price-CVD divergence (0=None, 1=Bullish, 2=Bearish)

Secondary ICT Context (The Map):
- ict_confluence_score: Cumulative bonus from ICT presence
- structure_state: M5 Structure (BOS=3, CHoCH=2, CHoCH_PENDING=1, NONE=0)
- regime_type: Market regime (TRENDING=2, RANGING=1, VOLATILE=3, NORMAL=0)
- proximity_to_liquidity: Distance to nearest swing high/low

Other Features:
- Pattern type, Score, HTF alignment, Session, ATR
"""
from typing import Dict, List, Optional, Tuple
import numpy as np
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger

logger = get_logger(__name__)


class LogisticRegressionModel:
    """
    Logistic Regression model for predicting trade win probability.
    
    Features used:
    - Pattern type (SWEEP=0, WALL=1, ZONE=2)
    - Score (raw)
    - HTF alignment (0/1)
    - CVD strength (0-4)
    - OI change (0-3)
    - Volume ratio (0-5)
    - Entry position (0-3)
    - Zone quality (0-5)
    - Session (ASIA=0, LONDON=1, NY=2, OTHER=3)
    
    Output: P(Win) between 0-1
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # Model parameters (learned from data)
        self.weights: Dict[str, float] = {}
        self.bias: float = 0.0
        
        # Training data
        self.training_data: List[Dict] = []
        self.min_trades_for_training = 50
        
        # Model state
        self.is_trained: bool = False
        self.last_update: Optional[datetime] = None
        
        # Default weights (before training - based on domain knowledge)
        self.weights = self._init_default_weights()
        
        # Load persisted model
        self.load_model()
    
    def _init_default_weights(self) -> Dict[str, float]:
        """Initialize default weights based on domain knowledge (Flow-First priority)"""
        weights = {
            # Primary Flow Features (The Engine) - HIGH PRIORITY
            'oi_shock': 0.35,           # OI change - primary signal
            'cvd_aggression': 0.30,     # CVD Delta - strong predictor
            'volume_surge_ratio': 0.20, # Volume confirmation
            'wall_imbalance': 0.25,     # Wall strength ratio
            'funding_bias': 0.15,       # Contrarian indicator
            'delta_divergence': 0.20,   # Price-CVD divergence
            
            # Secondary ICT Context (The Map) - BONUS
            'ict_confluence_score': 0.15,       # ICT pattern bonuses
            'structure_state': 0.10,            # M5 structure
            'regime_type': 0.12,               # Market regime
            'proximity_to_liquidity': 0.08,    # Distance to liquidity
            
            # Basic Features
            'pattern_sweep': 0.0,    # One-hot encoded
            'pattern_wall': 0.0,
            'pattern_zone': 0.0,
            'score': 0.15,
            'htf_aligned': 0.20,
            'entry_position': 0.10,
            'zone_quality': 0.12,
            'session_asia': 0.0,
            'session_london': 0.0,
            'session_ny': 0.0,
            'session_other': 0.0,
            'atr_distance': -0.08,
        }
        self.bias = -0.3
        return weights
    
    def extract_features(self, signal_data: Dict, market_data: Dict) -> np.ndarray:
        """Extract feature vector from signal and market data (Flow-First)"""
        features = []
        
        # === PRIMARY FLOW FEATURES (The Engine) ===
        
        # 1. oi_shock: Normalized OI change (-1 to 1 typically)
        oi_shock = market_data.get('oi_shock', market_data.get('oi_change', 0))
        oi_norm = np.clip(oi_shock / 0.5, -1, 1)  # Normalize to -1 to 1
        features.append(oi_norm)
        
        # 2. cvd_aggression: CVD Delta relative to volatility
        cvd_aggression = market_data.get('cvd_aggression', market_data.get('cvd_delta', 0))
        cvd_norm = np.clip(cvd_aggression / 2.0, -1, 1)  # Normalize
        features.append(cvd_norm)
        
        # 3. volume_surge_ratio: Current volume vs 50-period MA
        vol_ratio = market_data.get('volume_surge_ratio', market_data.get('volume_ratio', 1))
        vol_norm = np.clip((vol_ratio - 1) / 2.0, -1, 1)  # Centered on 1
        features.append(vol_norm)
        
        # 4. wall_imbalance: Bid vs Ask wall strength ratio (-1 to 1)
        # >0 = more bid walls, <0 = more ask walls
        bid_walls = market_data.get('bid_walls_value', 0)
        ask_walls = market_data.get('ask_walls_value', 0)
        if bid_walls + ask_walls > 0:
            wall_imbalance = (bid_walls - ask_walls) / (bid_walls + ask_walls)
        else:
            wall_imbalance = 0
        features.append(wall_imbalance)
        
        # 5. funding_bias: Funding rate (-1 to 1, negative = shorts paid, positive = longs paid)
        funding = market_data.get('funding_bias', 0)
        funding_norm = np.clip(funding / 0.001, -1, 1)  # Normalize typical funding
        features.append(funding_norm)
        
        # 6. delta_divergence_signal: Price-CVD divergence (0=None, 1=Bullish, 2=Bearish)
        div_signal = signal_data.get('delta_divergence_signal', 0)
        features.append(div_signal / 2.0)  # Normalize to 0-1
        
        # === SECONDARY ICT CONTEXT (The Map) ===
        
        # 7. ict_confluence_score: Cumulative ICT bonuses (0-5)
        ict_score = signal_data.get('ict_confluence_score', signal_data.get('ict_score', 0))
        features.append(ict_score / 5.0)
        
        # 8. structure_state: M5 Structure (BOS=3, CHoCH=2, CHoCH_PENDING=1, NONE=0)
        structure = signal_data.get('structure_state', 0)
        features.append(structure / 3.0)
        
        # 9. regime_type: Market regime (TRENDING=2, RANGING=1, VOLATILE=3, NORMAL=0)
        regime = signal_data.get('regime_type', 0)
        features.append(regime / 3.0)
        
        # 10. proximity_to_liquidity: Distance to nearest liquidity (0=far, 1=very close)
        prox = signal_data.get('proximity_to_liquidity', 0.5)
        features.append(prox)
        
        # === BASIC FEATURES ===
        
        # 11. Pattern type (one-hot) - FIX: Issue 4 - Metadata Naming
        # Updated: SWEEP -> LP, WALL -> DB, ZONE -> DA
        pattern = signal_data.get('pattern_type', 'DA')
        pattern_map = {
            # Institutional naming (primary)
            'LP': [1, 0, 0],  # Liquidity Purge
            'DB': [0, 1, 0],  # Defensive Block
            'DA': [0, 0, 1],  # Delta Absorption
            # Legacy naming (backward compatibility)
            'SWEEP': [1, 0, 0],   # -> LP
            'WALL': [0, 1, 0],    # -> DB
            'ZONE': [0, 0, 1],    # -> DA
            'OI_MOM': [1, 0, 0],  # -> LP
            'CVD_REV': [0, 0, 1]  # -> DA
        }
        features.extend(pattern_map.get(pattern, [0, 0, 0]))
        
        # 12. Score (normalized 0-1)
        score = signal_data.get('score', 10)
        features.append(min(score / 20.0, 1.0))
        
        # 13. HTF alignment (0/1)
        htf_aligned = 1 if signal_data.get('is_trend_aligned', True) else 0
        features.append(htf_aligned)
        
        # 14. Entry position (0-3 normalized)
        entry_pos = signal_data.get('entry_position_score', 1)
        features.append(entry_pos / 3.0)
        
        # 15. Zone quality (0-5 normalized)
        zone_q = signal_data.get('zone_quality', 1)
        features.append(zone_q / 5.0)
        
        # 16. Session (one-hot)
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 8:
            session = [1, 0, 0, 0]  # Asia
        elif 8 <= hour < 13:
            session = [0, 1, 0, 0]  # London
        elif 13 <= hour < 21:
            session = [0, 0, 1, 0]  # NY
        else:
            session = [0, 0, 0, 1]  # Other
        features.extend(session)
        
        # 17. ATR distance (normalized, negative = wider ATR = worse)
        atr = market_data.get('atr', 100)
        atr_norm = min(atr / 500.0, 1.0) if atr else 0.5
        features.append(-atr_norm)  # Negative because wider ATR = worse
        
        return np.array(features)
    
    def sigmoid(self, z: float) -> float:
        """Sigmoid function"""
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))
    
    def predict_proba(self, features: np.ndarray) -> float:
        """Predict win probability"""
        if not self.is_trained:
            # Use default weights before training
            z = self.bias
            for i, (key, w) in enumerate(self.weights.items()):
                if i < len(features):
                    z += w * features[i]
        else:
            # Use learned weights - map to feature order
            weight_keys = [
                'oi_shock', 'cvd_aggression', 'volume_surge_ratio', 'wall_imbalance',
                'funding_bias', 'delta_divergence', 'ict_confluence_score', 'structure_state',
                'regime_type', 'proximity_to_liquidity', 'pattern_sweep', 'pattern_wall',
                'pattern_zone', 'score', 'htf_aligned', 'entry_position', 'zone_quality',
                'session_asia', 'session_london', 'session_ny', 'session_other', 'atr_distance'
            ]
            z = self.bias
            for i, key in enumerate(weight_keys):
                if i < len(features) and key in self.weights:
                    z += self.weights[key] * features[i]
        
        return self.sigmoid(z)
    
    def predict(self, signal_data: Dict, market_data: Dict) -> Dict:
        """Predict win probability for a signal"""
        features = self.extract_features(signal_data, market_data)
        win_prob = self.predict_proba(features)
        
        # Determine recommendation based on probability
        if win_prob >= 0.6:
            recommendation = 'HIGH_CONFIDENCE'
        elif win_prob >= 0.5:
            recommendation = 'MODERATE'
        elif win_prob >= 0.4:
            recommendation = 'LOW_CONFIDENCE'
        else:
            recommendation = 'AVOID'
        
        return {
            'win_probability': win_prob,
            'recommendation': recommendation,
            'confidence_label': self._get_confidence_label(win_prob),
            'features': features.tolist() if hasattr(features, 'tolist') else list(features)
        }
    
    def _get_confidence_label(self, prob: float) -> str:
        """Get human-readable confidence label"""
        if prob >= 0.7:
            return "STRONG BUY" if prob > 0.5 else "STRONG SELL"
        elif prob >= 0.55:
            return "MODERATE BUY" if prob > 0.5 else "MODERATE SELL"
        elif prob >= 0.45:
            return "NEUTRAL"
        else:
            return "LOW OPPORTUNITY"
    
    def record_trade(self, signal_data: Dict, market_data: Dict, outcome: str) -> bool:
        """
        Record a trade outcome for training.
        
        Args:
            signal_data: Signal that was traded
            market_data: Market conditions at entry
            outcome: 'WIN' or 'LOSS'
        
        Returns:
            True if recorded successfully
        """
        outcome_val = 1.0 if outcome.upper() == 'WIN' else 0.0
        
        record = {
            'signal': signal_data,
            'market': market_data,
            'outcome': outcome_val,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'pattern': signal_data.get('pattern_type', 'UNKNOWN'),
            'score': signal_data.get('score', 0),
            'rr': market_data.get('rr', 0)
        }
        
        self.training_data.append(record)
        
        # Keep only last 1000 records
        if len(self.training_data) > 1000:
            self.training_data = self.training_data[-1000:]
        
        # Save to disk
        self.save_training_data()
        
        # Retrain if we have enough data
        if len(self.training_data) >= self.min_trades_for_training:
            self.train()
        
        logger.debug(f"Recorded trade: {outcome} | Total trades: {len(self.training_data)}")
        return True
    
    def train(self) -> bool:
        """Train the logistic regression model using gradient descent"""
        if len(self.training_data) < self.min_trades_for_training:
            logger.debug(f"Not enough data to train: {len(self.training_data)}/{self.min_trades_for_training}")
            return False
        
        try:
            # Prepare training data
            X = []
            y = []
            
            for record in self.training_data:
                features = self.extract_features(record['signal'], record['market'])
                X.append(features)
                y.append(record['outcome'])
            
            X = np.array(X)
            y = np.array(y)
            
            # Simple gradient descent
            learning_rate = 0.01
            n_iterations = 500
            
            n_features = X.shape[1]
            
            # Initialize weights
            w = np.zeros(n_features)
            b = 0.0
            
            for _ in range(n_iterations):
                # Forward pass
                z = np.dot(X, w) + b
                p = self.sigmoid(z)
                
                # Compute gradients
                dw = np.dot(X.T, (p - y)) / len(y)
                db = np.mean(p - y)
                
                # Update weights
                w -= learning_rate * dw
                b -= learning_rate * db
            
            # Store learned weights with feature names
            feature_names = [
                'oi_shock', 'cvd_aggression', 'volume_surge_ratio', 'wall_imbalance',
                'funding_bias', 'delta_divergence', 'ict_confluence_score', 'structure_state',
                'regime_type', 'proximity_to_liquidity', 'pattern_sweep', 'pattern_wall',
                'pattern_zone', 'score', 'htf_aligned', 'entry_position', 'zone_quality',
                'session_asia', 'session_london', 'session_ny', 'session_other', 'atr_distance'
            ]
            
            self.weights = {}
            for i, name in enumerate(feature_names):
                if i < len(w):
                    self.weights[name] = w[i]
                else:
                    # Use default weight if feature not available
                    self.weights[name] = self._init_default_weights().get(name, 0.0)
            self.bias = b
            
            self.is_trained = True
            self.last_update = datetime.now(timezone.utc)
            
            # Calculate training accuracy
            predictions = self.sigmoid(np.dot(X, w) + b)
            accuracy = np.mean((predictions >= 0.5) == (y >= 0.5))
            
            logger.info(f"Logistic Regression trained | Samples: {len(y)} | Accuracy: {accuracy:.1%}")
            
            # Save model
            self.save_model()
            
            return True
            
        except Exception as e:
            logger.error(f"Error training model: {e}")
            return False
    
    def get_model_stats(self) -> Dict:
        """Get model statistics"""
        if not self.training_data:
            return {
                'is_trained': False,
                'total_trades': 0,
                'message': 'No training data'
            }
        
        wins = sum(1 for r in self.training_data if r['outcome'] == 1.0)
        losses = len(self.training_data) - wins
        
        return {
            'is_trained': self.is_trained,
            'total_trades': len(self.training_data),
            'wins': wins,
            'losses': losses,
            'win_rate': wins / len(self.training_data) if self.training_data else 0,
            'last_update': self.last_update.isoformat() if self.last_update else None,
            'weights': {k: round(v, 4) for k, v in list(self.weights.items())[:5]},  # Top 5
            'bias': round(self.bias, 4)
        }
    
    def get_performance_by_pattern(self) -> Dict:
        """Get win rate breakdown by pattern"""
        patterns = {}
        for record in self.training_data:
            pattern = record.get('pattern', 'UNKNOWN')
            if pattern not in patterns:
                patterns[pattern] = {'wins': 0, 'total': 0}
            patterns[pattern]['total'] += 1
            if record['outcome'] == 1.0:
                patterns[pattern]['wins'] += 1
        
        result = {}
        for pattern, data in patterns.items():
            result[pattern] = {
                'wins': data['wins'],
                'total': data['total'],
                'win_rate': data['wins'] / data['total'] if data['total'] > 0 else 0
            }
        
        return result
    
    def save_training_data(self):
        """Save training data to disk"""
        try:
            path = Path('data/logistic_training_data.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save as JSON-compatible
            with open(path, 'w') as f:
                json.dump(self.training_data[-500:], f, indent=2)  # Keep last 500
        except Exception as e:
            logger.debug(f"Could not save training data: {e}")
    
    def save_model(self):
        """Save model weights to disk"""
        try:
            path = Path('data/logistic_model.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            
            model_data = {
                'weights': self.weights,
                'bias': self.bias,
                'is_trained': self.is_trained,
                'last_update': self.last_update.isoformat() if self.last_update else None
            }
            
            with open(path, 'w') as f:
                json.dump(model_data, f, indent=2)
                
            logger.debug(f"Model saved to {path}")
        except Exception as e:
            logger.debug(f"Could not save model: {e}")
    
    def load_model(self):
        """Load model weights from disk"""
        try:
            path = Path('data/logistic_model.json')
            if not path.exists():
                return
            
            with open(path, 'r') as f:
                model_data = json.load(f)
            
            self.weights = model_data.get('weights', {})
            self.bias = model_data.get('bias', -0.5)
            self.is_trained = model_data.get('is_trained', False)
            
            if self.last_update:
                self.last_update = datetime.fromisoformat(model_data.get('last_update'))
            
            logger.info(f"Model loaded | Trained: {self.is_trained}")
        except Exception as e:
            logger.debug(f"Could not load model: {e}")
    
    def load_training_data(self):
        """Load training data from disk"""
        try:
            path = Path('data/logistic_training_data.json')
            if not path.exists():
                return
            
            with open(path, 'r') as f:
                self.training_data = json.load(f)
            
            logger.info(f"Training data loaded: {len(self.training_data)} records")
        except Exception as e:
            logger.debug(f"Could not load training data: {e}")
