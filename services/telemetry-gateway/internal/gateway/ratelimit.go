package gateway

import (
	"sync"
	"time"
)

type clientWindow struct {
	start time.Time
	count int
}

type RateLimiter struct {
	mu      sync.Mutex
	limit   int
	window  time.Duration
	clients map[string]clientWindow
	now     func() time.Time
}

func NewRateLimiter(limit int, window time.Duration) *RateLimiter {
	return &RateLimiter{
		limit:   limit,
		window:  window,
		clients: make(map[string]clientWindow),
		now:     time.Now,
	}
}

func (r *RateLimiter) Allow(client string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()

	now := r.now()
	current, exists := r.clients[client]
	if !exists || now.Sub(current.start) >= r.window {
		r.clients[client] = clientWindow{start: now, count: 1}
		return true
	}
	if current.count >= r.limit {
		return false
	}
	current.count++
	r.clients[client] = current
	return true
}
