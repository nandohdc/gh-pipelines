package main

func add(a int, b int) int {
	return a + b
}

func integrationOnly(a int) int {
	return a * 2
}

func main() {
	_ = add(1, 2)
}
