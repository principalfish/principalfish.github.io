var filters = {
  state : {
    "leave" : [0, 100]
  },

  selectHandle : function(parameter, criteria){

    parameter = parameter.substring(7); // trim filter- off of parameters


    if (criteria == "null"){
      delete filters.state[parameter]
    } else {
      if (parameter == "region" && criteria == "england"){
        filters.state["region"] = regionMap["england"]
      } else {
        filters.state[parameter] = criteria;
      }
    }

    filters.filter();
  },

  leave : function(parameter, value){
    if (parameter == "low"){
      filters.state.leave[0] = value;
    } else if (parameter == "high"){
      filters.state.leave[1] = value;
    }
    filters.filter()
  },

  opacities : {},

  filter : function(){

    $.each(currentMap.seatData, function(seat, data){
      var meetsCriteria = true


      $.each(filters.state, function(parameter, criteria){

        if (parameter == "leave"){
          var leave = data.leave;

          if (leave < filters.state["leave"][0] || leave > filters.state["leave"][1] ){
            meetsCriteria = false;
          }

        }  else if (parameter == "current") {
          if (data.current != criteria){
            meetsCriteria = false;
          }

        } else {

          //if (data.seatInfo[parameter] != criteria){ // TO DO CHECK NOT BROKEN
          if (criteria.indexOf(data[parameter]) == - 1){
            meetsCriteria = false;
          }
        }
      });

      if (meetsCriteria == true){
        var current = currentMap.seatData[seat]["current"];
        filters.opacities[seat] = (20 / 3) * ((currentMap.seatData[seat][current] / 100) - 0.5) ;
        data.filtered = true;
      } else {
        filters.opacities[seat] = 0;
        data.filtered = false;
      }
    })

    filters.display();

  },

  display: function(){
    $.each(filters.opacities, function(seat, opacity){

      var mapSelect = currentMap.seatData[seat].mapSelect;
      mapSelect.opacity = opacity;
      d3.select("#i" + mapSelect.properties.info_id).attr("opacity", null);
      d3.select("#i" + mapSelect.properties.info_id).attr("fill-opacity", opacity);
      if (opacity == 0){
        d3.select("#i" + mapSelect.properties.info_id).attr("opacity", 0.02);
      }


    });

    // for table
    filters.filteredList = [];
    $.each(currentMap.seatData, function(seat, data){
      if (data.filtered == true){
        currentMap.seatData[seat]["name"] = seat
        filters.filteredList.push(currentMap.seatData[seat]);

      }
    });

    $("#seatlist-sort" + voteTotals.activeSort).removeClass("sort-active");
    voteTotals.activeSort = "leave";
    $("#seatlist-sortleave").addClass("sort-active");

    voteTotals.display(filters.filteredList);
  },

  reset : function(){
    filters.state = {"leave" : [0, 100]}; // reset filters state
    // reset ui
    $("#filter-current option:eq(0)").prop("selected", true);
    $("#filter-winner2015 option:eq(0)").prop("selected", true);
  	$("#filter-region option:eq(0)").prop("selected", true);
  	$("#filter-leave").get(0).reset();

    // reset map
    filters.filter();
  },

  filteredList : []



}
