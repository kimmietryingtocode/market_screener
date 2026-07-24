package handlers

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PortfolioHandler struct {
	DB            *pgxpool.Pool
	QuantBaseURL  string
	HTTPClient    *http.Client
}

type holdingInput struct {
	Ticker string  `json:"ticker" binding:"required"`
	Weight float64 `json:"weight" binding:"required,gt=0,lte=1"`
}

type createPortfolioRequest struct {
	Name     string         `json:"name" binding:"required"`
	Holdings []holdingInput `json:"holdings" binding:"required,min=1,dive"`
}

// CreatePortfolio inserts the portfolio + holdings inside one transaction.
// Weight-sum validation happens here in the handler (not the DB) because
// "your weights sum to 1.03, fix holding X" is a much better error message
// than a constraint violation.
func (h *PortfolioHandler) CreatePortfolio(c *gin.Context) {
	userID := c.GetInt64("user_id")

	var req createPortfolioRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	var sum float64
	for _, h := range req.Holdings {
		sum += h.Weight
	}
	if sum < 0.999 || sum > 1.001 {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": fmt.Sprintf("holding weights must sum to 1.0, got %.4f", sum),
		})
		return
	}

	ctx := context.Background()
	tx, err := h.DB.Begin(ctx)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to start transaction"})
		return
	}
	defer tx.Rollback(ctx)

	var portfolioID int64
	err = tx.QueryRow(ctx,
		`INSERT INTO portfolios (user_id, name) VALUES ($1, $2) RETURNING id`,
		userID, req.Name,
	).Scan(&portfolioID)
	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"error": "you already have a portfolio with that name"})
		return
	}

	for _, hd := range req.Holdings {
		_, err = tx.Exec(ctx,
			`INSERT INTO holdings (portfolio_id, ticker, weight) VALUES ($1, $2, $3)`,
			portfolioID, hd.Ticker, hd.Weight,
		)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to insert holdings"})
			return
		}
	}

	if err := tx.Commit(ctx); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to commit transaction"})
		return
	}

	c.JSON(http.StatusCreated, gin.H{"id": portfolioID, "name": req.Name, "holdings": req.Holdings})
}

func (h *PortfolioHandler) ListPortfolios(c *gin.Context) {
	userID := c.GetInt64("user_id")

	rows, err := h.DB.Query(context.Background(),
		`SELECT id, name, created_at FROM portfolios WHERE user_id = $1 ORDER BY created_at DESC`,
		userID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to fetch portfolios"})
		return
	}
	defer rows.Close()

	type portfolioRow struct {
		ID        int64     `json:"id"`
		Name      string    `json:"name"`
		CreatedAt time.Time `json:"created_at"`
	}
	var out []portfolioRow
	for rows.Next() {
		var p portfolioRow
		if err := rows.Scan(&p.ID, &p.Name, &p.CreatedAt); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to scan portfolio"})
			return
		}
		out = append(out, p)
	}

	c.JSON(http.StatusOK, out)
}

// --- Analytics: this is the Go -> Python boundary ---

type quantAnalyticsRequest struct {
	Holdings []holdingInput `json:"holdings"`
	Lookback string         `json:"lookback"` // e.g. "1y"
}

// GetAnalytics fetches holdings from Postgres, forwards them to the
// Python quant service, and returns the computed result. No caching
// yet — that's the deliberate next step once this round-trip is proven
// to work and is slow enough to be worth caching.
func (h *PortfolioHandler) GetAnalytics(c *gin.Context) {
	userID := c.GetInt64("user_id")
	portfolioID := c.Param("id")

	rows, err := h.DB.Query(context.Background(),
		`SELECT ticker, weight FROM holdings
		 WHERE portfolio_id = $1
		 AND portfolio_id IN (SELECT id FROM portfolios WHERE user_id = $2)`,
		portfolioID, userID,
	)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to fetch holdings"})
		return
	}
	defer rows.Close()

	var holdings []holdingInput
	for rows.Next() {
		var hd holdingInput
		if err := rows.Scan(&hd.Ticker, &hd.Weight); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to scan holding"})
			return
		}
		holdings = append(holdings, hd)
	}

	if len(holdings) == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "portfolio not found or has no holdings"})
		return
	}

	payload, _ := json.Marshal(quantAnalyticsRequest{Holdings: holdings, Lookback: "1y"})

	client := h.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Second}
	}

	resp, err := client.Post(h.QuantBaseURL+"/analytics/portfolio", "application/json", bytes.NewReader(payload))
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "quant service unreachable"})
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		c.JSON(http.StatusBadGateway, gin.H{"error": "quant service returned an error"})
		return
	}

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to decode quant service response"})
		return
	}

	// Persist the run for history/audit; this is also the table Redis
	// will eventually front as a cache.
	resultJSON, _ := json.Marshal(result)
	_, _ = h.DB.Exec(context.Background(),
		`INSERT INTO analytics_runs (portfolio_id, result_json) VALUES ($1, $2)`,
		portfolioID, resultJSON,
	)

	c.JSON(http.StatusOK, result)
}
