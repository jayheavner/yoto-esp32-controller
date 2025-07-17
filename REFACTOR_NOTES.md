# Feature Addition Request Template

## Project Context

I have a working Python project for controlling Yoto audio players with a clean modular structure. I need to add specific new functionality while preserving all existing behavior.

### Current Working Code
- **Core modules** - Working API client and data models
- **Desktop UI modules** - Working Qt/QML interface with coordinator pattern
- **All functionality** - Currently working as expected
- **Project repo**: https://github.com/jayheavner/yoto-esp32-controller

### Current State
- Clean separation between core logic and UI
- No circular dependencies
- All modules pass pylint --errors-only
- Established patterns for data flow and UI integration

## FEATURE REQUIREMENT

Update client to track state through MQTT. Working code can be found in the `yoto_mvp.py`.
Make the following changes depending on state
 - Playing - If the current state is Playing then the Now Playing UI should show a Pause button
 - Paused - If the current state is Paused then the Now Playing UI should show a Play button
 - Any other condition - log the state so further scope/action can be determined.

## Feature Addition Goal

Add the specified functionality using existing architectural patterns while preserving all current behavior.

## CRITICAL CONSTRAINTS

### 1. SURGICAL CHANGES ONLY
- Modify **only what is necessary** for the specific feature
- Preserve **all existing functionality exactly**
- Changes should be **minimal and targeted** in diffs
- No refactoring, optimization, or "improvements" unless explicitly required
- If existing code works, don't touch it

### 2. INCREMENTAL PHASES MANDATORY
- Implement in **three distinct phases**:
  1. **Data/Navigation Foundation** - Backend changes, navigation setup
  2. **UI Implementation** - New screens/components with mock data
  3. **Integration** - Wire up real data flow
- Test **each phase independently** before proceeding
- If any phase test fails, stop and fix before continuing

### 3. ARCHITECTURAL COMPLIANCE
- **Core modules** may NOT import from UI modules
- **UI modules** may import from core modules
- **New modules** must follow existing import patterns
- **One UI concern** = one QML file/module
- Maintain existing coordinator pattern for Qt integration

### 4. SIMPLE CODE REQUIREMENTS
- Use `Any` for complex type annotations
- No elaborate type systems or advanced patterns
- Maximum simplicity - this is a home project
- Follow existing code style and patterns exactly

### 5. MUST PASS PYLINT
- Every modified/new module must pass `python -m pylint module.py --errors-only` with zero errors
- All imports must exist and be used
- All type annotations must be correct
- No placeholders or TODOs in final code

### 5. PROPER NAMING
- Artifacts must be named exactly as they appear in the GitHub repo. No artifacts should exist that do not match exactly as they appear in the GitHub repo.

### 5. FOR EACH TURN
- List all files that were modified in that turn
- Specify all functionality changed during that turn that needs to be tested

## Required Implementation Phases

### Phase 1: UI Implementation  
- Change appearance of play/pause icons to indicate next possible action.
- **Stop here** until confirmed working

## Success Criteria

### Functional Requirements
- All existing functionality works identically
- New feature works as specified
- No regressions in existing behavior
- Smooth integration with existing UI patterns

### Technical Requirements
- All modules pass pylint --errors-only with zero errors
- All phase tests pass
- No new circular dependencies
- Minimal, targeted changes visible in diffs
- Clean separation maintained between core and UI

### Quality Requirements
- New code follows existing patterns exactly
- UI components are properly isolated
- Data flow follows established coordinator pattern
- Error handling consistent with existing code

## Error Prevention Rules

1. **Stop immediately** if any phase test fails
2. **Ask before deviating** from existing patterns
3. **Test imports independently** before integration
4. **Use absolute imports** following existing conventions
5. **Preserve existing interfaces** unless modification essential
6. **Create minimal changes** - if it works, don't change it
7. **Follow existing file organization** and naming conventions

## Required Deliverable Format

**Before starting implementation:**
1. **Analysis**: List all files that will be modified/created and specific reason for each
2. **Phase breakdown**: Detailed plan for each of the 3 phases
3. **Dependencies**: Confirmation of what existing code will be imported/used
4. **Questions**: Any clarifications needed before proceeding

**For each phase:**
1. **Code artifacts** for all changes in that phase
2. **Test instructions** to verify phase completion
3. **Import verification** commands to run

**Final delivery:**
- Working feature integrated with existing system
- All tests passing
- Documentation of what was changed and why

## Critical Success Mantra

**"Add only what is asked for. Change only what is necessary. Test every step. Stop and ask when unclear."**

**Start with Analysis phase: List exactly what files you plan to modify/create and why, break down the 3 implementation phases, and ask any questions before writing any code.**

## Finishing up

 - After the first two phases have deemed complete, ask the user if any other changes need to be made.
 - Evaluate the scope of the requested change and depending on scope, prompt the user if a new conversation should be started to make the change or make the change if the scope is limited and related to the work being done.
 - When the user indicates they are done making changes, please do the following:
   1. List all files changed during the conversation
   2. Generate commit comments for the changes that have been made