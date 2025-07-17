"""
Grid mathematics for 3-column card layout.

Calculate row/column positions, determine visible cards in viewport,
and handle grid dimension calculations. No UI framework dependencies -
works across Qt desktop and ESP32 embedded displays.
"""
from dataclasses import dataclass
from typing import List, Tuple, Optional
import math


@dataclass(slots=True)
class GridDimensions:
    """Grid layout dimensions and spacing."""
    columns: int = 3
    cell_width: int = 150
    cell_height: int = 200
    margin: int = 10
    spacing: int = 10
    
    @property
    def total_cell_width(self) -> int:
        """Total width including spacing for one cell."""
        return self.cell_width + self.spacing
    
    @property
    def total_cell_height(self) -> int:
        """Total height including spacing for one cell."""
        return self.cell_height + self.spacing
    
    @property
    def grid_width(self) -> int:
        """Total width required for the grid."""
        return (self.margin * 2) + (self.columns * self.cell_width) + ((self.columns - 1) * self.spacing)


@dataclass(slots=True)
class GridPosition:
    """Position of an item in the grid."""
    row: int
    column: int
    x: int
    y: int
    index: int
    
    def __str__(self) -> str:
        return f"GridPosition(row={self.row}, col={self.column}, x={self.x}, y={self.y}, idx={self.index})"


@dataclass(slots=True)
class ViewportInfo:
    """Information about the current viewport."""
    x: int = 0
    y: int = 0
    width: int = 480
    height: int = 800
    
    @property
    def bottom(self) -> int:
        """Bottom edge of viewport."""
        return self.y + self.height
    
    @property
    def right(self) -> int:
        """Right edge of viewport."""
        return self.x + self.width


class GridLayout:
    """
    Manages grid layout calculations for card display.
    
    Provides methods for calculating positions, determining visible items,
    and handling scrolling. Designed to work with any UI framework or
    direct rendering system.
    """
    
    def __init__(self, dimensions: GridDimensions) -> None:
        """
        Initialize grid layout with specified dimensions.
        
        Args:
            dimensions: Grid dimensions and spacing configuration
        """
        self.dimensions = dimensions
    
    def get_position(self, index: int) -> GridPosition:
        """
        Calculate grid position for item at given index.
        
        Args:
            index: Zero-based index of item
            
        Returns:
            GridPosition with row, column, and pixel coordinates
        """
        row = index // self.dimensions.columns
        column = index % self.dimensions.columns
        
        x = (self.dimensions.margin + 
             column * (self.dimensions.cell_width + self.dimensions.spacing))
        y = (self.dimensions.margin + 
             row * (self.dimensions.cell_height + self.dimensions.spacing))
        
        return GridPosition(
            row=row,
            column=column,
            x=x,
            y=y,
            index=index
        )
    
    def get_positions_batch(self, start_index: int, count: int) -> List[GridPosition]:
        """
        Calculate positions for a batch of items efficiently.
        
        Args:
            start_index: Starting index for batch
            count: Number of positions to calculate
            
        Returns:
            List of GridPosition objects
        """
        positions = []
        for i in range(count):
            index = start_index + i
            positions.append(self.get_position(index))
        return positions
    
    def get_total_height(self, item_count: int) -> int:
        """
        Calculate total height needed for given number of items.
        
        Args:
            item_count: Number of items to display
            
        Returns:
            Total height in pixels
        """
        if item_count == 0:
            return self.dimensions.margin * 2
        
        rows = math.ceil(item_count / self.dimensions.columns)
        return (self.dimensions.margin * 2 + 
                rows * self.dimensions.cell_height + 
                (rows - 1) * self.dimensions.spacing)
    
    def get_visible_range(self, viewport: ViewportInfo, total_items: int) -> Tuple[int, int]:
        """
        Calculate range of items visible in viewport.
        
        Args:
            viewport: Current viewport information
            total_items: Total number of items available
            
        Returns:
            Tuple of (start_index, end_index) for visible items
        """
        if total_items == 0:
            return (0, 0)
        
        # Calculate which rows are visible
        top_visible_y = max(0, viewport.y - self.dimensions.margin)
        bottom_visible_y = viewport.bottom + self.dimensions.margin
        
        # Account for spacing between cells
        cell_total_height = self.dimensions.cell_height + self.dimensions.spacing
        
        # Calculate row range (0-based)
        start_row = max(0, top_visible_y // cell_total_height)
        end_row = min(
            math.ceil(total_items / self.dimensions.columns) - 1,
            bottom_visible_y // cell_total_height
        )
        
        # Convert to item indices
        start_index = start_row * self.dimensions.columns
        end_index = min(total_items, (end_row + 1) * self.dimensions.columns)
        
        return (start_index, end_index)
    
    def get_visible_positions(self, viewport: ViewportInfo, total_items: int) -> List[GridPosition]:
        """
        Get positions for all items visible in viewport.
        
        Args:
            viewport: Current viewport information
            total_items: Total number of items available
            
        Returns:
            List of GridPosition objects for visible items
        """
        start_index, end_index = self.get_visible_range(viewport, total_items)
        
        if start_index >= end_index:
            return []
        
        return self.get_positions_batch(start_index, end_index - start_index)
    
    def find_item_at_position(self, x: int, y: int, total_items: int) -> Optional[int]:
        """
        Find item index at given pixel coordinates.
        
        Args:
            x: X coordinate (pixel)
            y: Y coordinate (pixel)
            total_items: Total number of items available
            
        Returns:
            Item index if found, None if no item at position
        """
        # Adjust for margin
        grid_x = x - self.dimensions.margin
        grid_y = y - self.dimensions.margin
        
        # Check if within grid bounds
        if grid_x < 0 or grid_y < 0:
            return None
        
        # Calculate cell position accounting for spacing
        cell_total_width = self.dimensions.cell_width + self.dimensions.spacing
        cell_total_height = self.dimensions.cell_height + self.dimensions.spacing
        
        column = grid_x // cell_total_width
        row = grid_y // cell_total_height
        
        # Check if within valid column range
        if column >= self.dimensions.columns:
            return None
        
        # Check if click is in spacing area (not on actual cell)
        cell_local_x = grid_x % cell_total_width
        cell_local_y = grid_y % cell_total_height
        
        if (cell_local_x >= self.dimensions.cell_width or 
            cell_local_y >= self.dimensions.cell_height):
            return None
        
        # Calculate item index
        index = row * self.dimensions.columns + column
        
        # Check if index is valid
        if index >= total_items:
            return None
        
        return index
    
    def get_scroll_position_for_item(self, index: int, viewport: ViewportInfo) -> int:
        """
        Calculate optimal scroll position to show specific item.
        
        Args:
            index: Index of item to show
            viewport: Viewport dimensions
            
        Returns:
            Y scroll position to center item in viewport
        """
        position = self.get_position(index)
        
        # Center the item in viewport
        center_y = position.y + (self.dimensions.cell_height // 2)
        scroll_y = center_y - (viewport.height // 2)
        
        # Ensure scroll position is valid (not negative)
        return max(0, scroll_y)
    
    def update_dimensions(self, new_dimensions: GridDimensions) -> None:
        """
        Update grid dimensions (useful for responsive layouts).
        
        Args:
            new_dimensions: New grid dimensions to use
        """
        self.dimensions = new_dimensions
    
    def get_optimal_cell_size(self, container_width: int, target_columns: int = 3) -> Tuple[int, int]:
        """
        Calculate optimal cell size for given container width.
        
        Args:
            container_width: Available container width
            target_columns: Desired number of columns
            
        Returns:
            Tuple of (cell_width, cell_height) optimized for container
        """
        # Account for margins and spacing
        available_width = container_width - (2 * self.dimensions.margin)
        spacing_width = (target_columns - 1) * self.dimensions.spacing
        
        # Calculate cell width
        cell_width = (available_width - spacing_width) // target_columns
        
        # Maintain aspect ratio (4:3 is common for card layouts)
        cell_height = int(cell_width * 1.33)
        
        return (max(1, cell_width), max(1, cell_height))