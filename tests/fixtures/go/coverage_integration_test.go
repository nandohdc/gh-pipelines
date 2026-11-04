//go:build integration

package main

import "testing"

func TestIntegrationOnly(t *testing.T) {
	if got := integrationOnly(4); got != 8 {
		t.Fatalf("integrationOnly(4) = %d, want 8", got)
	}
}
