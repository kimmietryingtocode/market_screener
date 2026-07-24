package config

import (
	"os"
)

// Config holds everything the API needs to boot. Keeping this as one
// struct loaded once at startup (vs. scattered os.Getenv calls) means
// you can see your entire runtime surface area in one place — useful
// when you're explaining the system to someone in a demo.
type Config struct {
	Port           string
	DatabaseURL    string
	JWTSecret      string
	QuantServiceURL string
	RedisURL       string
}

func Load() Config {
	return Config{
		Port:            getEnv("PORT", "8080"),
		DatabaseURL:     getEnv("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/portfolio?sslmode=disable"),
		JWTSecret:       getEnv("JWT_SECRET", "dev-secret-change-me"),
		QuantServiceURL: getEnv("QUANT_SERVICE_URL", "http://localhost:8000"),
		RedisURL:        getEnv("REDIS_URL", "redis://localhost:6379"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
