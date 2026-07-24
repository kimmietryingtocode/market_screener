package db

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

// NewPool opens a pgx connection pool. Using pgx directly (not an ORM)
// is the right call here — the schema is small and stable, and raw SQL
// in handlers is easier to defend in an interview than "the ORM did it."
func NewPool(ctx context.Context, databaseURL string) (*pgxpool.Pool, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		return nil, err
	}
	return pool, nil
}
