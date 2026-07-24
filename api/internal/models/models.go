package models

import "time"

type User struct {
	ID           int64     `json:"id"`
	Email        string    `json:"email"`
	PasswordHash string    `json:"-"` // never serialize this, even by accident
	CreatedAt    time.Time `json:"created_at"`
}

type Portfolio struct {
	ID        int64     `json:"id"`
	UserID    int64     `json:"user_id"`
	Name      string    `json:"name"`
	Holdings  []Holding `json:"holdings,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

type Holding struct {
	ID          int64   `json:"id"`
	PortfolioID int64   `json:"portfolio_id"`
	Ticker      string  `json:"ticker"`
	Weight      float64 `json:"weight"`
}

// AnalyticsResult mirrors the JSON shape returned by the Python quant
// service. Kept as a passthrough struct in Go — Go doesn't need to
// understand the internals of Sharpe/Sortino math, just store and
// forward it.
type AnalyticsResult struct {
	PortfolioID      int64              `json:"portfolio_id"`
	AnnualizedReturn float64            `json:"annualized_return"`
	Volatility       float64            `json:"volatility"`
	SharpeRatio      float64            `json:"sharpe_ratio"`
	MaxDrawdown      float64            `json:"max_drawdown"`
	ComputedAt       time.Time          `json:"computed_at"`
	Extra            map[string]float64 `json:"extra,omitempty"`
}
