package main

func add(a int, b int) int {
	return a + b
}

func integrationOnly(a int) int {
	doubled := a * 2
	shifted := doubled + 1
	return shifted
}

func main() {
	_ = add(1, 2)
	_ = integrationOnly(4)
}
