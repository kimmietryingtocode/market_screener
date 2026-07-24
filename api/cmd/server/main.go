package main

import (
	"context"
	"log"

	"github.com/gin-gonic/gin"

	"github.com/imbaothu/portfolio-platform/api/internal/config"
	"github.com/imbaothu/portfolio-platform/api/internal/db"
	"github.com/imbaothu/portfolio-platform/api/internal/handlers"
	"github.com/imbaothu/portfolio-platform/api/internal/middleware"
)

func main() {
	cfg := config.Load()

	pool, err := db.NewPool(context.Background(), cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("failed to connect to database: %v", err)
	}
	defer pool.Close()

	authHandler := &handlers.AuthHandler{DB: pool, JWTSecret: cfg.JWTSecret}
	portfolioHandler := &handlers.PortfolioHandler{DB: pool, QuantBaseURL: cfg.QuantServiceURL}

	r := gin.Default()

	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(200, gin.H{"status": "ok"})
	})

	v1 := r.Group("/api/v1")
	{
		v1.POST("/auth/register", authHandler.Register)
		v1.POST("/auth/login", authHandler.Login)

		authed := v1.Group("/")
		authed.Use(middleware.RequireAuth(cfg.JWTSecret))
		{
			authed.POST("/portfolios", portfolioHandler.CreatePortfolio)
			authed.GET("/portfolios", portfolioHandler.ListPortfolios)
			authed.GET("/portfolios/:id/analytics", portfolioHandler.GetAnalytics)
		}
	}

	log.Printf("listening on :%s", cfg.Port)
	if err := r.Run(":" + cfg.Port); err != nil {
		log.Fatalf("server failed: %v", err)
	}
}
