import QtQuick 6.7
import QtQuick.Controls 6.7

Item {
    id: root
    
    Rectangle {
        anchors.fill: parent
        color: "#111"
        
        // Back button - top left (larger for children)
        Button {
            id: backButton
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: 15
            width: 70
            height: 50
            
            background: Rectangle {
                color: backButton.pressed ? "#444" : "#333"
                radius: 8
            }
            
            // Left arrow icon (larger for children)
            Canvas {
                anchors.centerIn: parent
                width: 30
                height: 25
                
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.strokeStyle = "#fff"
                    ctx.lineWidth = 3
                    ctx.beginPath()
                    ctx.moveTo(20, 5)
                    ctx.lineTo(8, 12.5)
                    ctx.lineTo(20, 20)
                    ctx.stroke()
                }
            }
            
            onClicked: {
                console.log("Back button clicked")
                // Find the StackView through the parent hierarchy
                var stackView = root.parent
                if (stackView) {
                    stackView.pop()
                }
            }
        }
        
        // Main content area
        Item {
            anchors.fill: parent
            anchors.topMargin: 80  // Space for back button
            
            // Large artwork display - centered in upper portion (clickable)
            Item {
                id: artworkContainer
                width: 300
                height: 300
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: 40
                
                Image {
                    id: artworkImage
                    anchors.centerIn: parent
                    width: Math.min(parent.width, sourceSize.width > 0 ? sourceSize.width : parent.width)
                    height: Math.min(parent.height, sourceSize.height > 0 ? sourceSize.height : parent.height)
                    source: window.selectedCard ? window.selectedCard.imagePath : ""
                    fillMode: Image.PreserveAspectFit
                    
                    Rectangle {
                        anchors.fill: parent
                        color: "#444"
                        visible: artworkImage.status !== Image.Ready
                        radius: 8
                        
                        Text {
                            anchors.centerIn: parent
                            text: "Card\nArtwork"
                            color: "#888"
                            font.pixelSize: 24
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
                
                // Make artwork clickable to view chapters
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (window.selectedCard && window.selectedCard.cardId) {
                            console.log("Artwork clicked - checking chapters for card:", window.selectedCard.cardId)
                            
                            // Check if card has chapters before navigating
                            if (coordinator) {
                                var chapters = coordinator.get_chapters(window.selectedCard.cardId)
                                if (chapters && chapters.length > 0) {
                                    console.log("Found", chapters.length, "chapters, navigating to chapter list")
                                    var stackView = root.parent
                                    if (stackView) {
                                        stackView.push("ChapterListView.qml", {
                                            "cardId": window.selectedCard.cardId,
                                            "cardTitle": window.selectedCard.title || "Unknown"
                                        })
                                    }
                                } else {
                                    console.log("No chapters found for this card")
                                    // Could show a toast/message here in the future
                                }
                            }
                        }
                    }
                }
            }
            
            // Transport controls - positioned near bottom for easy reach
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 60  // Closer to bottom for children
                spacing: 40  // More spacing for larger buttons
                
                // Previous Chapter Button (larger for children)
                Button {
                    id: prevButton
                    width: 90
                    height: 90
                    
                    background: Rectangle {
                        color: prevButton.pressed ? "#555" : "#333"
                        radius: 45
                        border.color: "#666"
                        border.width: 2
                    }
                    
                    // Previous icon (double left triangle)
                    Canvas {
                        anchors.centerIn: parent
                        width: 50
                        height: 35
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            // First triangle
                            ctx.moveTo(25, 10)
                            ctx.lineTo(10, 17.5)
                            ctx.lineTo(25, 25)
                            ctx.closePath()
                            ctx.fill()
                            
                            // Second triangle
                            ctx.beginPath()
                            ctx.moveTo(40, 10)
                            ctx.lineTo(25, 17.5)
                            ctx.lineTo(40, 25)
                            ctx.closePath()
                            ctx.fill()
                        }
                    }
                    
                    onClicked: {
                        console.log("Previous chapter clicked")
                    }
                }
                
                // Play/Pause Button (larger for children)
                Button {
                    id: playButton
                    width: 90
                    height: 90
                    
                    property bool isPlaying: false  // State for play/pause toggle
                    
                    background: Rectangle {
                        color: playButton.pressed ? "#555" : "#333"
                        radius: 45
                        border.color: "#666"
                        border.width: 2
                    }
                    
                    // Play/Pause icon that switches based on state
                    Canvas {
                        anchors.centerIn: parent
                        width: 35
                        height: 35
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            if (playButton.isPlaying) {
                                // Pause icon (two rectangles)
                                ctx.fillRect(8, 6, 6, 23)
                                ctx.fillRect(21, 6, 6, 23)
                            } else {
                                // Play triangle icon
                                ctx.moveTo(10, 6)
                                ctx.lineTo(28, 17.5)
                                ctx.lineTo(10, 29)
                                ctx.closePath()
                                ctx.fill()
                            }
                        }
                        
                        // Redraw when state changes
                        Connections {
                            target: playButton
                            function onIsPlayingChanged() {
                                parent.requestPaint()
                            }
                        }
                    }
                    
                    onClicked: {
                        console.log("Play/Pause clicked")
                    }
                }
                
                // Next Chapter Button (larger for children)
                Button {
                    id: nextButton
                    width: 90
                    height: 90
                    
                    background: Rectangle {
                        color: nextButton.pressed ? "#555" : "#333"
                        radius: 45
                        border.color: "#666"
                        border.width: 2
                    }
                    
                    // Next icon (double right triangle)
                    Canvas {
                        anchors.centerIn: parent
                        width: 50
                        height: 35
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            // First triangle
                            ctx.moveTo(10, 10)
                            ctx.lineTo(25, 17.5)
                            ctx.lineTo(10, 25)
                            ctx.closePath()
                            ctx.fill()
                            
                            // Second triangle
                            ctx.beginPath()
                            ctx.moveTo(25, 10)
                            ctx.lineTo(40, 17.5)
                            ctx.lineTo(25, 25)
                            ctx.closePath()
                            ctx.fill()
                        }
                    }
                    
                    onClicked: {
                        console.log("Next chapter clicked")
                    }
                }
            }
        }
    }
}