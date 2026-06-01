import numpy as np


class parameter_grid:
    """
    A class that generates a grid of parameter combinations for hyperparameter tuning.

    Parameters:
    - search_space (dict): A dictionary containing the hyperparameter search space, where the keys are the parameter names and the values are lists of possible parameter values.
    - condition (function, optional): A function that takes a parameter combination as input and returns True if the combination satisfies a certain condition, or False otherwise.

    Usage:
    - Initialize an instance of parameter_grid with the search_space and condition (optional).
    - Iterate over the instance to get each parameter combination.

    Example:
    ```
    search_space = {
        'learning_rate': [0.01, 0.1, 1.0],
        'num_layers': [1, 2, 3],
        'hidden_units': [64, 128, 256]
    }
    grid = parameter_grid(search_space)
    for params in grid:
        # Do something with each parameter combination
        print(params)
    ```
    """

    def __init__(self, search_space, condition=None):
        self.search_space = search_space
        self.condition = condition
        self._index = 0
        self.grid = None

    def __iter__(self):
        if self.grid == None:
            self.grid = self.generate_grid()
        return self

    def __next__(self):
        if self._index < len(self.grid):
            self._index += 1
            return self.grid[self._index - 1]
        else:
            self._index = 0
            raise StopIteration

    def __len__(self):
        if self.grid == None:
            self.grid = self.generate_grid()
        return len(self.grid)

    def generate_grid(self):
        """
        Generates a grid of parameter combinations based on the search space.

        Returns:
        - parameters_grid (list): A list of dictionaries, where each dictionary represents a parameter combination.

        Algorithm:
        - Get the keys and values from the search_space dictionary.
        - Calculate the size of each parameter space.
        - Calculate the total size of the grid.
        - Iterate over all possible combinations and create a dictionary for each combination.
        - Apply the condition (if provided) to filter the parameter combinations.
        - Return the list of parameter combinations.
        """
        keys = list(self.search_space.keys())
        values = list(self.search_space.values())
        space_sizes = [len(v) for v in values]
        total_size = np.prod(space_sizes)
        parameters_grid = []
        for i in range(total_size):
            grid = {}
            level_idx = i
            for idx in reversed(range(len(keys))):
                space_idx = level_idx % space_sizes[idx]
                level_idx = level_idx // space_sizes[idx]
                grid[keys[idx]] = values[idx][space_idx]
            parameters_grid.append(dict(reversed(list(grid.items()))))
        if self.condition:
            parameters_grid = list(filter(self.condition, parameters_grid))
        return parameters_grid
